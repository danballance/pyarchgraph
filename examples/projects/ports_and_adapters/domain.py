"""A message is a domain value, independent of its delivery mechanism."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Message:
    recipient: str
    body: str
