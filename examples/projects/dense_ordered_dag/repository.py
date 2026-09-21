"""Persistence adapter."""
import model

def save_order(sku: str, quantity: int) -> model.Order:
    return model.Order(f"{sku}:{quantity}", quantity)
