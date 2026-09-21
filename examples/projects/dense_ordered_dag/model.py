from dataclasses import dataclass

@dataclass(frozen=True)
class Order:
    reference: str
    quantity: int
