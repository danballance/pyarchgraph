"""Formatting independent of order and inventory policy."""
def format_amount(cents: int) -> str:
    return f"GBP {cents / 100:.2f}"
