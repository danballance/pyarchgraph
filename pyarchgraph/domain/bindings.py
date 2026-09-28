"""Reconcile Python import bindings across explicitly selected roots."""

from collections import defaultdict
from dataclasses import replace
from pathlib import PurePosixPath

from pyarchgraph.domain.models import Diagnostic, Severity, SourceModule


class BindingReconciler:
    """Mark conflicting import bindings across selected source roots."""

    def reconcile(
        self,
        modules: tuple[SourceModule, ...],
    ) -> tuple[tuple[SourceModule, ...], tuple[Diagnostic, ...]]:
        by_name: dict[str, list[SourceModule]] = defaultdict(list)
        for module in modules:
            if module.import_name and module.binding_status == "bound":
                by_name[module.import_name].append(module)
        ambiguous = {name for name, items in by_name.items() if len(items) > 1}
        # A module selected in one root cannot be an ordinary package prefix in another.
        nonpackages = {
            name
            for name, items in by_name.items()
            if any(not item.is_package for item in items)
        }
        for name in by_name:
            parts = name.split(".")
            for index in range(1, len(parts)):
                prefix = ".".join(parts[:index])
                if prefix in nonpackages:
                    ambiguous.update((prefix, name))
                for package in by_name.get(prefix, ()):
                    if package.is_package and any(
                        not PurePosixPath(item.path).is_relative_to(
                            PurePosixPath(package.path).parent
                        )
                        for item in by_name[name]
                    ):
                        # Regular packages do not merge another root's namespace
                        # fragments into their __path__. Keep safe parent bindings.
                        ambiguous.add(name)
        diagnostics = tuple(
            Diagnostic(
                Severity.ERROR,
                "ambiguous_import_binding",
                f"Import name {name!r} has incompatible bindings across selected roots: "
                + ", ".join(item.path for item in by_name[name]),
                path=by_name[name][0].path,
            )
            for name in sorted(ambiguous)
        )
        return tuple(
            replace(module, binding_status="ambiguous")
            if module.import_name in ambiguous and module.binding_status == "bound"
            else module
            for module in modules
        ), diagnostics
