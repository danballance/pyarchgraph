import demo.api

from .model import Model


def serve() -> str:
    return Model.__name__
