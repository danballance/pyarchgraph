"""History also sends events to the hub."""
import events

NAME = "history"
def changed():
    return events.emit(NAME)
