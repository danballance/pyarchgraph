"""Certainty-aware comparisons of observed policy violations."""

from __future__ import annotations

from copy import deepcopy

from pyarchgraph.cleanup import CLEANUP_MODEL_VERSION, build_cleanup_report
from pyarchgraph.model import Severity, UnresolvedReason
from pyarchgraph.policy import DEFINITE_KINDS
from pyarchgraph.provenance import _validate_document


def _coverage_complete(document: dict) -> bool:
    analysis = document["analysis"]
    return bool(document["modules"]) and all(
        analysis[field]
        for field in ("complete", "scope_valid", "dependency_resolution_complete")
    )


def _validate_cleanup_document(document: dict, label: str) -> None:
    _validate_document(document, label)
    if document["schema_version"] != "0.4":
        raise ValueError(
            f"incompatible cleanup baseline: {label}.schema_version must be 0.4"
        )
    cleanup = document.get("cleanup")
    if (
        not isinstance(cleanup, dict)
        or cleanup.get("model_version") != CLEANUP_MODEL_VERSION
    ):
        raise ValueError(
            f"incompatible cleanup baseline: {label}.cleanup.model_version "
            f"must be {CLEANUP_MODEL_VERSION}"
        )
    if (
        document["analysis"]["provenance"].get("cleanup_model_version")
        != CLEANUP_MODEL_VERSION
    ):
        raise ValueError(
            f"incompatible cleanup baseline: {label}.analysis.provenance."
            f"cleanup_model_version must be {CLEANUP_MODEL_VERSION}"
        )
    _validate_coverage(document, label)


def _validate_coverage(document: dict, label: str) -> None:
    """Reject optimistic coverage flags contradicted by serialized evidence."""

    def require(condition: bool, field: str) -> None:
        if not condition:
            raise ValueError(
                f"incompatible cleanup baseline: {label}.{field} is missing, "
                "invalid, or inconsistent with coverage"
            )

    def string(value: object) -> bool:
        return isinstance(value, str) and bool(value)

    diagnostics = document.get("diagnostics")
    require(isinstance(diagnostics, list), "diagnostics")
    for index, diagnostic in enumerate(diagnostics):
        field = f"diagnostics[{index}]"
        require(isinstance(diagnostic, dict), field)
        require(
            diagnostic.get("severity") in [item.value for item in Severity],
            field + ".severity",
        )
        require(string(diagnostic.get("code")), field + ".code")
        require(isinstance(diagnostic.get("message"), str), field + ".message")
        if "path" in diagnostic:
            require(string(diagnostic["path"]), field + ".path")
        for name, minimum in (("line", 1), ("column", 0)):
            if name in diagnostic:
                require(
                    type(diagnostic[name]) is int and diagnostic[name] >= minimum,
                    field + "." + name,
                )

    unresolved = document.get("unresolved_imports")
    require(isinstance(unresolved, list), "unresolved_imports")
    facts = {fact["id"]: fact for fact in document["import_facts"]}
    module_ids = {module["id"] for module in document["modules"]}
    for index, item in enumerate(unresolved):
        field = f"unresolved_imports[{index}]"
        require(isinstance(item, dict), field)
        require(
            string(item.get("source")) and item["source"] in module_ids,
            field + ".source",
        )
        require(string(item.get("requested")), field + ".requested")
        require(
            item.get("reason") in [reason.value for reason in UnresolvedReason],
            field + ".reason",
        )
        fact_ids = item.get("fact_ids")
        require(isinstance(fact_ids, list) and bool(fact_ids), field + ".fact_ids")
        require(
            all(
                string(fact_id)
                and fact_id in facts
                and facts[fact_id]["source"] == item["source"]
                for fact_id in fact_ids
            ),
            field + ".fact_ids",
        )
        require(len(fact_ids) == len(set(fact_ids)), field + ".fact_ids")

    limitations = document.get("limitations")
    require(
        isinstance(limitations, list)
        and all(isinstance(item, str) for item in limitations),
        "limitations",
    )
    analysis = document["analysis"]
    has_error = any(item["severity"] == "error" for item in diagnostics)
    invalid_scope = any(
        item["code"] in {"expected_package_missing", "source_root_mismatch"}
        for item in diagnostics
    )
    require(not analysis["complete"] or not has_error, "analysis.complete")
    require(not analysis["scope_valid"] or not invalid_scope, "analysis.scope_valid")
    if analysis["dependency_resolution_complete"]:
        require(
            analysis["complete"]
            and analysis["scope_valid"]
            and all(
                item["reason"] == "namespace_base_unmodelled" for item in unresolved
            )
            and not any(
                item["code"] == "dynamic_import_ignored" for item in diagnostics
            )
            and all(
                any(
                    proof["resolution_kind"] in DEFINITE_KINDS
                    for proof in edge["evidence"]
                )
                for edge in document["architecture_dependencies"]
            ),
            "analysis.dependency_resolution_complete",
        )
    check = document.get("check")
    require(isinstance(check, dict), "check")
    if any(finding["certainty"] == "definite" for finding in document["findings"]):
        expected_status = "fail"
    elif not _coverage_complete(document) or document["findings"]:
        expected_status = "needs_review"
    else:
        expected_status = "pass"
    require(check.get("status") == expected_status, "check.status")


def compare_cleanup(
    current: dict, baseline: dict, *, allow_inventory_change: bool = False
) -> dict:
    """Compare typed violations without mistaking lost certainty for repair.

    Saved counts and violation lists are derived data: both cleanup reports are
    rebuilt from validated graph evidence. Incomplete analyses can still explain
    known changes, but cannot verify disappearances or clear review requirements.
    Neither the documents nor their nested evidence records are modified.
    """
    _validate_cleanup_document(baseline, "baseline")
    _validate_cleanup_document(current, "current analysis")
    previous = baseline["analysis"]["provenance"]
    present = current["analysis"]["provenance"]
    differences = sorted(
        key
        for key in set(previous) | set(present)
        if previous.get(key) != present.get(key)
    )
    if differences:
        raise ValueError(
            "incompatible cleanup baseline: " + ", ".join(differences) + " differs"
        )

    old_modules = {(module["id"], module["path"]) for module in baseline["modules"]}
    new_modules = {(module["id"], module["path"]) for module in current["modules"]}
    inventory_changed = old_modules != new_modules
    if inventory_changed and not allow_inventory_change:
        raise ValueError(
            "incompatible cleanup baseline: module inventory changed; review "
            "additions/removals and use --allow-inventory-change"
        )

    before = build_cleanup_report(baseline)
    after = build_cleanup_report(current)

    def atoms(report: dict) -> dict[tuple[str, str, str], dict]:
        return {
            (item["kind"], item["source"], item["target"]): item
            for item in report["violations"]
        }

    old_atoms = atoms(before)
    new_atoms = atoms(after)
    transitions: dict[str, list[dict]] = {
        name: []
        for name in (
            "newly_observed",
            "newly_confirmed",
            "lost_certainty",
            "verified_resolved",
            "disappeared_unverified",
            "removed_with_module",
            "persistent",
        )
    }
    current_complete = _coverage_complete(current)
    current_module_ids = {module["id"] for module in current["modules"]}
    removed_module_ids = {
        module["id"] for module in baseline["modules"]
    } - current_module_ids
    for key in sorted(old_atoms.keys() | new_atoms.keys()):
        old = old_atoms.get(key)
        new = new_atoms.get(key)
        if old is None:
            transition, record = "newly_observed", new
        elif new is None:
            record = old
            if not {old["source"], old["target"]} <= current_module_ids:
                transition = "removed_with_module"
            # Removing an intermediate module can erase a cycle between retained
            # endpoints. A dangling flat import may then look external rather
            # than unresolved, so even apparently complete coverage is not
            # sufficient to verify a repair across module deletions.
            elif current_complete and not removed_module_ids:
                transition = "verified_resolved"
            else:
                transition = "disappeared_unverified"
        elif old["certainty"] == new["certainty"]:
            transition, record = "persistent", new
        elif new["certainty"] == "definite":
            transition, record = "newly_confirmed", new
        else:
            transition, record = "lost_certainty", new
        transitions[transition].append(deepcopy(record))

    needs_review = bool(
        not _coverage_complete(baseline)
        or not current_complete
        or before["possible_violation_count"]
        or after["possible_violation_count"]
        or transitions["newly_confirmed"]
        or transitions["lost_certainty"]
        or transitions["disappeared_unverified"]
        or transitions["removed_with_module"]
        or inventory_changed
    )
    return {
        "compatible": True,
        "model_version": CLEANUP_MODEL_VERSION,
        "before_violation_count": before["violation_count"],
        "current_violation_count": after["violation_count"],
        "count_delta": after["violation_count"] - before["violation_count"],
        "has_new_violations": bool(transitions["newly_observed"]),
        "needs_review": needs_review,
        "added_modules": [list(item) for item in sorted(new_modules - old_modules)],
        "removed_modules": [list(item) for item in sorted(old_modules - new_modules)],
        **transitions,
    }
