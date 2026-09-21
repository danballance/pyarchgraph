"""The hub reaches back into both clients, joining one SCC."""
import editor
import history

def emit(source):
    return (source, editor.NAME, history.NAME)
