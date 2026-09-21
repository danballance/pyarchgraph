"""Application behavior receives its delivery implementation."""
import domain
import ports

def welcome(recipient: str, delivery: ports.Delivery) -> None:
    delivery.send(domain.Message(recipient, "Welcome!"))
