"""Load the frozen reference collector against the relocated domain models.

Only its model import is adapted in memory. The reference implementation on
disk remains byte-for-byte unchanged and is still hashed by benchmarks.
"""

import sys
from pathlib import Path
from types import ModuleType

_reference = ModuleType("benchmarks._frozen_reference_extraction")
_reference.__file__ = str(Path(__file__).with_name("reference_extraction.py"))
sys.modules[_reference.__name__] = _reference
_source = Path(_reference.__file__).read_text(encoding="utf-8")
exec(
    compile(
        _source.replace(
            "from pyarchgraph.model import", "from pyarchgraph.domain.model import"
        ),
        _reference.__file__,
        "exec",
    ),
    _reference.__dict__,
)
ReferenceFactSource = _reference.AstImportFactSource
