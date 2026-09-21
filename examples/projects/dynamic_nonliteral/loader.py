"""The caller supplies the plugin module name at runtime."""
import importlib as il
from importlib import import_module as load_module

def select(module_name: str):
    return il.import_module(module_name)

def fallback(module_name: str):
    return load_module(module_name)
