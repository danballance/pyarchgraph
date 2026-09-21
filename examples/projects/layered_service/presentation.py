"""HTTP-independent entry point for submitting an order."""
import service

def submit(sku: str, quantity: int) -> str:
    return service.place_order(sku, quantity).reference
