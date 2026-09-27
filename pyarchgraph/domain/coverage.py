"""Mandatory source-coverage policy, independent of optional analysis checks."""

from pyarchgraph.domain.model import Diagnostic, Severity, TargetBoundary


class CoveragePolicy:
    def diagnostics(
        self, diagnostics: tuple[Diagnostic, ...]
    ) -> tuple[Diagnostic, ...]:
        return tuple(
            sorted(
                set(diagnostics),
                key=lambda item: (
                    item.severity.value,
                    item.code,
                    item.path or "",
                    item.line or 0,
                    item.column or 0,
                    item.message,
                ),
            )
        )

    def is_complete(
        self,
        diagnostics: tuple[Diagnostic, ...],
        boundaries: tuple[TargetBoundary, ...],
    ) -> bool:
        return not any(item.severity is Severity.ERROR for item in diagnostics) and all(
            boundary.acknowledged for boundary in boundaries
        )
