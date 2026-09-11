__version__ = "2.5.5.1"


def __getattr__(name):
    """Lazy-load main and Client to avoid circular imports."""
    if name == "main":
        from .cli import main

        return main
    if name == "Client":
        from .qopy import Client

        return Client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
