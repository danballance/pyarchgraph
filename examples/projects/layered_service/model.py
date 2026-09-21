"""Domain values have no dependency on their callers."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Order:
    reference: str
    quantity: int
