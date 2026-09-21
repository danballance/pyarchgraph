"""A demonstration adapter with no network side effects."""
import domain

class EmailDelivery:
    def send(self, message: domain.Message) -> None:
        print(f"To: {message.recipient}: {message.body}")
