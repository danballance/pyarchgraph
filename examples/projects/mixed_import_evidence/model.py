"""The domain incorrectly imports an application-service default."""
import service

class Order:
    def __init__(self):
        self.state = service.DEFAULT_STATE
