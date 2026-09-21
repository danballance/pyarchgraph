"""Portable analysis provenance and conservative baseline comparisons."""

from dataclasses import asdict
from fnmatch import fnmatchcase
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import subprocess

import networkx as nx

from pyarchgraph.discovery import DEFAULT_EXCLUDED_DIRECTORY_BASENAMES
from pyarchgraph.model import ImportScope, ImportSyntax, ResolutionKind
from pyarchgraph.policy import DEFINITE_KINDS, GRAPH_POLICY_VERSION, GraphPolicy
from pyarchgraph.quality import FORMULA_VERSION


def analyser_identity() -> dict:
    package = Path(__file__).parent
    digest = hashlib.sha256()
    for source in sorted(package.glob("*.py")):
        digest.update(source.name.encode())
        digest.update(b"\0")
        digest.update(source.read_bytes())
    try:
        package_version = version("pyarchgraph")
    except PackageNotFoundError:
        package_version = "uninstalled"
    commit = None
    if (package.parent / ".git").exists():
        try:
            process = subprocess.run(
                ["git", "-C", str(package.parent), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if process.returncode == 0:
                commit = process.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "version": package_version,
        "commit": commit,
        "source_digest": digest.hexdigest(),
    }


def build_provenance(
    source_root: str,
    python_version: str,
    excludes: tuple[str, ...],
    expected_packages: tuple[str, ...],
    policy: GraphPolicy,
    forbidden_dependencies: tuple[tuple[str, str], ...],
    *,
    fact_source_name: str,
) -> dict:
    return {
        "analyser": analyser_identity(),
        "formula_version": FORMULA_VERSION,
        "graph_policy_version": GRAPH_POLICY_VERSION,
        "source_root": source_root,
        "python_version": python_version,
        "excludes": sorted(set(excludes)),
        "default_excluded_directories": sorted(DEFAULT_EXCLUDED_DIRECTORY_BASENAMES),
        "expected_packages": sorted(set(expected_packages)),
        "graph_policy": asdict(policy),
        "forbidden_dependencies": [
            list(rule) for rule in sorted(set(forbidden_dependencies))
        ],
        "fact_source": fact_source_name,
    }


def _cyclic_edges(
    pairs: set[tuple[str, str]],
) -> dict[frozenset[str], set[tuple[str, str]]]:
    graph = nx.DiGraph()
    graph.add_edges_from(pairs)
    return {
        frozenset(members): {
            (source, target)
            for source in members
            for target in graph.successors(source)
            if target in members
        }
        for members in nx.strongly_connected_components(graph)
        if len(members) > 1 or graph.has_edge(next(iter(members)), next(iter(members)))
    }


def _validate_document(document: dict, label: str, *, graph: bool = True) -> None:
    """Validate the fields used in comparisons, including graph consistency.

    Baselines are external JSON. Empty or omitted finding lists must not erase
    cycles recorded by the graph; evidence must resolve to actual import facts.
    Presentation-only fields such as the diagram are deliberately not required.
    """

    def require(condition: bool, field: str) -> None:
        if not condition:
            raise ValueError(
                f"incompatible baseline: {label}.{field} is missing or invalid"
            )

    def mapping(value: object, field: str) -> dict:
        require(isinstance(value, dict), field)
        return value

    def sequence(value: object, field: str) -> list:
        require(isinstance(value, list), field)
        return value

    def string(value: object) -> bool:
        return isinstance(value, str) and bool(value)

    def strings(value: object, field: str) -> list[str]:
        values = sequence(value, field)
        require(all(string(item) for item in values), field)
        return values

    def pairs(value: object, field: str) -> set[tuple[str, str]]:
        values = sequence(value, field)
        require(
            all(
                isinstance(item, list)
                and len(item) == 2
                and all(string(part) for part in item)
                for item in values
            ),
            field,
        )
        result = {tuple(item) for item in values}
        require(len(result) == len(values), field)
        return result

    mapping(document, "document")
    require(string(document.get("schema_version")), "schema_version")
    analysis = mapping(document.get("analysis"), "analysis")
    for name in ("complete", "scope_valid", "dependency_resolution_complete"):
        require(type(analysis.get(name)) is bool, f"analysis.{name}")
    provenance = mapping(analysis.get("provenance"), "analysis.provenance")
    for name in (
        "formula_version",
        "graph_policy_version",
        "source_root",
        "python_version",
        "fact_source",
    ):
        require(string(provenance.get(name)), f"analysis.provenance.{name}")
    for name in ("excludes", "default_excluded_directories", "expected_packages"):
        strings(provenance.get(name), f"analysis.provenance.{name}")
    analyser = mapping(provenance.get("analyser"), "analysis.provenance.analyser")
    for name in ("version", "source_digest"):
        require(string(analyser.get(name)), f"analysis.provenance.analyser.{name}")
    require(
        "commit" in analyser
        and (analyser["commit"] is None or string(analyser["commit"])),
        "analysis.provenance.analyser.commit",
    )
    policy = mapping(provenance.get("graph_policy"), "analysis.provenance.graph_policy")
    for name in ("include_type_only", "include_local", "include_tests"):
        require(
            type(policy.get(name)) is bool, f"analysis.provenance.graph_policy.{name}"
        )
    rules = pairs(
        provenance.get("forbidden_dependencies"),
        "analysis.provenance.forbidden_dependencies",
    )
    if not graph:
        return

    module_ids = set()
    for index, value in enumerate(sequence(document.get("modules"), "modules")):
        field = f"modules[{index}]"
        module = mapping(value, field)
        require(string(module.get("id")) and string(module.get("path")), field)
        require(module["id"] not in module_ids, field + ".id")
        require(type(module.get("is_package")) is bool, field + ".is_package")
        require(
            "parent_package" in module
            and (module["parent_package"] is None or string(module["parent_package"])),
            field + ".parent_package",
        )
        module_ids.add(module["id"])

    facts = {}
    for index, value in enumerate(
        sequence(document.get("import_facts"), "import_facts")
    ):
        field = f"import_facts[{index}]"
        fact = mapping(value, field)
        require(string(fact.get("id")) and fact["id"] not in facts, field + ".id")
        require(
            string(fact.get("source")) and fact["source"] in module_ids,
            field + ".source",
        )
        require(string(fact.get("path")), field + ".path")
        for name in ("line", "column", "alias_index", "relative_level"):
            require(
                type(fact.get(name)) is int
                and fact[name] >= (1 if name == "line" else 0),
                field + "." + name,
            )
        for name in ("end_line", "end_column"):
            require(
                name in fact
                and (
                    fact[name] is None
                    or (
                        type(fact[name]) is int
                        and fact[name] >= (1 if name == "end_line" else 0)
                    )
                ),
                field + "." + name,
            )
        for name in ("source_segment", "base_module", "imported_name", "as_name"):
            require(
                name in fact and (fact[name] is None or isinstance(fact[name], str)),
                field + "." + name,
            )
        require(isinstance(fact.get("bound_name"), str), field + ".bound_name")
        require(type(fact.get("type_only")) is bool, field + ".type_only")
        require(
            fact.get("syntax") in [kind.value for kind in ImportSyntax],
            field + ".syntax",
        )
        require(
            fact.get("scope") in [kind.value for kind in ImportScope], field + ".scope"
        )
        facts[fact["id"]] = fact

    edges = {}
    definite_pairs = set()
    for index, value in enumerate(
        sequence(document.get("architecture_dependencies"), "architecture_dependencies")
    ):
        field = f"architecture_dependencies[{index}]"
        edge = mapping(value, field)
        require(string(edge.get("source")) and string(edge.get("target")), field)
        pair = (edge["source"], edge["target"])
        require(set(pair) <= module_ids and pair not in edges, field)
        evidence = sequence(edge.get("evidence"), field + ".evidence")
        require(bool(evidence), field + ".evidence")
        evidence_ids = set()
        for item in evidence:
            mapping(item, field + ".evidence")
            require(
                string(item.get("fact_id")) and item["fact_id"] in facts,
                field + ".evidence.fact_id",
            )
            require(
                item.get("resolution_kind") in [kind.value for kind in ResolutionKind],
                field + ".evidence.resolution_kind",
            )
            require(
                facts[item["fact_id"]]["source"] == pair[0], field + ".evidence.source"
            )
            evidence_ids.add((item["fact_id"], item["resolution_kind"]))
            if item["resolution_kind"] in DEFINITE_KINDS:
                definite_pairs.add(pair)
        require(len(evidence_ids) == len(evidence), field + ".evidence")
        edges[pair] = evidence_ids

    components = _cyclic_edges(set(edges))
    definite_components = _cyclic_edges(definite_pairs)
    definite_cycle_pairs = (
        set().union(*definite_components.values()) if definite_components else set()
    )
    definite_members = (
        set().union(*definite_components) if definite_components else set()
    )
    seen_components = set()
    seen_forbidden = set()
    finding_ids = set()
    for index, value in enumerate(sequence(document.get("findings"), "findings")):
        field = f"findings[{index}]"
        finding = mapping(value, field)
        require(
            string(finding.get("id")) and finding["id"] not in finding_ids,
            field + ".id",
        )
        finding_ids.add(finding["id"])
        require(
            finding.get("kind") in ("cycle", "forbidden_dependency"), field + ".kind"
        )
        require(
            finding.get("certainty") in ("definite", "possible"), field + ".certainty"
        )
        witness = sequence(finding.get("witness"), field + ".witness")
        require(bool(witness), field + ".witness")
        witness_pairs = []
        for item in witness:
            mapping(item, field + ".witness")
            require(
                string(item.get("id"))
                and string(item.get("source"))
                and string(item.get("target")),
                field + ".witness",
            )
            pair = (item["source"], item["target"])
            require(pair in edges, field + ".witness")
            witness_pairs.append(pair)
            evidence = sequence(item.get("evidence"), field + ".witness.evidence")
            require(bool(evidence), field + ".witness.evidence")
            for proof in evidence:
                mapping(proof, field + ".witness.evidence")
                require(
                    string(proof.get("id")) and string(proof.get("resolution_kind")),
                    field + ".witness.evidence",
                )
                require(
                    (proof["id"], proof["resolution_kind"]) in edges[pair],
                    field + ".witness.evidence",
                )
                require(
                    {
                        key: value
                        for key, value in proof.items()
                        if key != "resolution_kind"
                    }
                    == facts[proof["id"]],
                    field + ".witness.evidence",
                )
        if finding["kind"] == "cycle":
            members = frozenset(strings(finding.get("members"), field + ".members"))
            require(
                members in components and members not in seen_components,
                field + ".members",
            )
            seen_components.add(members)
            require(
                pairs(
                    finding.get("cyclic_dependencies"), field + ".cyclic_dependencies"
                )
                == components[members],
                field + ".cyclic_dependencies",
            )
            require(
                pairs(
                    finding.get("definite_cyclic_dependencies"),
                    field + ".definite_cyclic_dependencies",
                )
                == components[members] & definite_cycle_pairs,
                field + ".definite_cyclic_dependencies",
            )
            require(
                set(
                    strings(
                        finding.get("definite_members"), field + ".definite_members"
                    )
                )
                == members & definite_members,
                field + ".definite_members",
            )
            require(
                finding["certainty"]
                == ("definite" if members & definite_members else "possible"),
                field + ".certainty",
            )
            require(
                len(witness_pairs) <= len(members)
                and len({pair[0] for pair in witness_pairs}) == len(witness_pairs)
                and all(
                    source in members
                    and target == witness_pairs[(i + 1) % len(witness_pairs)][0]
                    for i, (source, target) in enumerate(witness_pairs)
                ),
                field + ".witness",
            )
            if finding["certainty"] == "definite":
                require(
                    all(pair in definite_pairs for pair in witness_pairs),
                    field + ".witness",
                )
        else:
            require(
                len(witness_pairs) == 1 and witness_pairs[0] not in seen_forbidden,
                field + ".witness",
            )
            pair = witness_pairs[0]
            seen_forbidden.add(pair)
            matched = {
                rule
                for rule in rules
                if fnmatchcase(pair[0], rule[0]) and fnmatchcase(pair[1], rule[1])
            }
            require(
                bool(matched)
                and pairs(finding.get("rules"), field + ".rules") == matched,
                field + ".rules",
            )
            require(
                finding["certainty"]
                == ("definite" if pair in definite_pairs else "possible"),
                field + ".certainty",
            )
    require(
        seen_components == set(components),
        "findings (cycle components do not match graph)",
    )
    require(
        seen_forbidden
        == {
            pair
            for pair in edges
            if any(
                fnmatchcase(pair[0], rule[0]) and fnmatchcase(pair[1], rule[1])
                for rule in rules
            )
        },
        "findings (forbidden dependencies do not match graph)",
    )


def compare_baseline(
    current: dict, baseline: dict, *, allow_inventory_change: bool = False
) -> dict:
    """Reject incompatible comparisons and compare dependency meaning, not sites.

    Every inventory change is explicit and requires opt-in. This prevents a
    deleted or excluded package from silently clearing a saved violation.
    """
    _validate_document(baseline, "baseline", graph=False)
    _validate_document(current, "current analysis", graph=False)
    if baseline["schema_version"] != current["schema_version"]:
        raise ValueError("incompatible baseline: schema_version differs")
    previous = baseline["analysis"]["provenance"]
    present = current["analysis"]["provenance"]
    differences = sorted(
        key
        for key in set(previous) | set(present)
        if previous.get(key) != present.get(key)
    )
    if differences:
        raise ValueError(
            "incompatible baseline: " + ", ".join(differences) + " differs"
        )
    for label, document in (("baseline", baseline), ("current analysis", current)):
        analysis = document.get("analysis", {})
        if not analysis.get("complete") or not analysis.get("scope_valid"):
            raise ValueError(
                f"incompatible baseline: {label} is incomplete or has invalid scope"
            )
        if not document.get("modules"):
            raise ValueError(f"incompatible baseline: {label} has an empty inventory")
        _validate_document(document, label)
    try:
        old_modules = {(module["id"], module["path"]) for module in baseline["modules"]}
        new_modules = {(module["id"], module["path"]) for module in current["modules"]}
        old_edges = {
            (edge["source"], edge["target"])
            for edge in baseline["architecture_dependencies"]
        }
        new_edges = {
            (edge["source"], edge["target"])
            for edge in current["architecture_dependencies"]
        }
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "incompatible baseline: missing or invalid graph inventory"
        ) from exc
    if old_modules != new_modules and not allow_inventory_change:
        raise ValueError(
            "incompatible baseline: module inventory changed; review additions/removals and use --allow-inventory-change"
        )

    def cyclic(document: dict, key: str) -> set[tuple[str, str]]:
        return {
            tuple(pair)
            for finding in document["findings"]
            if finding["kind"] == "cycle"
            for pair in finding[key]
        }

    added = new_edges - old_edges
    old_cycles = cyclic(baseline, "definite_cyclic_dependencies")
    new_cycles = cyclic(current, "definite_cyclic_dependencies")
    for finding in current["findings"]:
        for edge in finding["witness"]:
            edge["new_dependency"] = (edge["source"], edge["target"]) in added
    return {
        "compatible": True,
        "added_modules": [list(item) for item in sorted(new_modules - old_modules)],
        "removed_modules": [list(item) for item in sorted(old_modules - new_modules)],
        "added_dependencies": [list(item) for item in sorted(added)],
        "removed_dependencies": [list(item) for item in sorted(old_edges - new_edges)],
        "new_definite_cyclic_dependencies": [
            list(item) for item in sorted(new_cycles - old_cycles)
        ],
        "resolved_definite_cyclic_dependencies": [
            list(item) for item in sorted(old_cycles - new_cycles)
        ],
    }
