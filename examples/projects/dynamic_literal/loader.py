"""Two standard dynamic import spellings with visible literal targets."""
import importlib

def plugin():
    return importlib.import_module("plugin")

def legacy_plugin():
    return __import__("plugin")
