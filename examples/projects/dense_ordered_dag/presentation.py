"""Presentation bypasses the service and repository boundaries."""
import service
import repository
import model

def submit(sku: str, quantity: int) -> model.Order:
    return repository.save_order(sku, quantity) if quantity else service.place_order(sku, quantity)
