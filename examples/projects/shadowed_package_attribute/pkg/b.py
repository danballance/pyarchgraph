"""A separate module that is not the imported package attribute."""
import pkg.a

def answer() -> int:
    return pkg.a.answer()
