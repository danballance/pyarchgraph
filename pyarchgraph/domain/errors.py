"""Errors at application and extension boundaries."""


class AnalysisError(ValueError):
    """Invalid analysis configuration; source failures instead return a report."""


class ExtensionError(RuntimeError):
    """A registered strategy raised or returned an invalid result."""
