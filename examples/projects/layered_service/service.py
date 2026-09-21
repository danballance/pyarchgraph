"""Application workflow; persistence details belong to the repository."""
import repository

def place_order(sku: str, quantity: int):
    return repository.save_order(sku, quantity)
