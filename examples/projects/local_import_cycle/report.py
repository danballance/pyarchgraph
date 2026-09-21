"""Report rendering requests a cached title lazily."""
TITLE = "Sales"

def render() -> str:
    import cache
    return cache.title()
