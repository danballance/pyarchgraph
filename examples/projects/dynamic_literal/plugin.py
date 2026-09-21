"""A dynamically loaded plugin reaches back into its loader."""
import loader

def name():
    return loader.__name__
