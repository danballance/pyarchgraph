"""Order pricing reaches into stock policy, creating a cycle."""
import inventory

ORDER_SURCHARGE = 2

def quote(sku: str) -> int:
    return inventory.stock_price(sku) + ORDER_SURCHARGE
