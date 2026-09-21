"""Customer annotation refers to orders without runtime import."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import orders

class Customer:
    orders: list[orders.Order]
