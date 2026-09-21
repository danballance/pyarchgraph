"""Stock policy improperly reaches back into order constants."""
import orders

def stock_price(sku: str) -> int:
    return len(sku) + orders.ORDER_SURCHARGE
