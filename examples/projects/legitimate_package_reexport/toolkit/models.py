"""Implementation of the public item value."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Item:
    label: str
