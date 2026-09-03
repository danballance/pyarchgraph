from typing import TYPE_CHECKING

import demo.service
import demo.service as service_alias

if TYPE_CHECKING:
    from .model import Model


def endpoint() -> str:
    return service_alias.serve()
