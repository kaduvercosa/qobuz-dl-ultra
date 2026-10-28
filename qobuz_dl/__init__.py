from typing import Any

__version__ = "2.6.7.3"


def __getattr__(name: str) -> Any:
    """Lazy-load main and Client to avoid circular imports."""
    if name == "main":
        from .cli import main

        return main
    if name == "Client":
        from .qopy import Client

        return Client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
