"""Common aliases should retain dynamic-import visibility."""
import importlib as il
from importlib import import_module as load_module
from importlib import import_module

def via_module_alias():
    return il.import_module("plugin")

def via_function_alias():
    return load_module("plugin")

def via_imported_function():
    return import_module("plugin")
