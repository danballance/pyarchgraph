"""Both ordinary and annotation-only imports support service -> model."""
from typing import TYPE_CHECKING
import model
if TYPE_CHECKING:
    import model as annotation_model

DEFAULT_STATE = "new"

def build() -> model.Order:
    return model.Order()
