"""An accidental self-import that a static analyser must preserve."""
import registry

HANDLERS = {}

def register(name, handler):
    registry.HANDLERS[name] = handler
