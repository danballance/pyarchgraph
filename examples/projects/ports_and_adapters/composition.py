"""The outermost module chooses concrete infrastructure."""
import email_adapter
import workflow

def main() -> None:
    workflow.welcome("reader@example.invalid", email_adapter.EmailDelivery())
