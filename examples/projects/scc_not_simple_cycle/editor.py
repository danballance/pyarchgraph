"""Editor registers changes through the event hub."""
import events

NAME = "editor"
def changed():
    return events.emit(NAME)
