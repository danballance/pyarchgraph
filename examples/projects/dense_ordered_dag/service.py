"""Application workflow also imports the domain value directly."""
import repository
import model

def place_order(sku: str, quantity: int) -> model.Order:
    return repository.save_order(sku, quantity)
