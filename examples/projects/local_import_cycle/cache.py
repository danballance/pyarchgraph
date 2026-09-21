"""Cache lookup imports report metadata lazily."""
def title() -> str:
    import report
    return report.TITLE
