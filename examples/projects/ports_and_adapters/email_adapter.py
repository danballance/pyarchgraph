"""A demonstration adapter with no network side effects."""
import domain
import ports

class EmailDelivery(ports.Delivery):
    def send(self, message: domain.Message) -> None:
        print(f"To: {message.recipient}: {message.body}")
