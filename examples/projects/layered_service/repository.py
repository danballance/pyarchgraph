"""A small in-memory adapter used by the example."""
import model

def save_order(sku: str, quantity: int) -> model.Order:
    return model.Order(reference=f"{sku}:{quantity}", quantity=quantity)
