"""An independent receipt formatter."""
import currency

def format_receipt(amount: int) -> str:
    return currency.format_amount(amount)
