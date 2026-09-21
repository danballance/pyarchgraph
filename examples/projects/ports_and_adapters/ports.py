"""Outbound delivery contract."""
from typing import Protocol
import domain

class Delivery(Protocol):
    def send(self, message: domain.Message) -> None: ...
