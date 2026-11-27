import sys as _sys
from typing import Any

if "--atalho" in _sys.argv:  # modo Atalho do iOS: avisar o iOS já na partida
    from qobuz_dl import _partida

    _partida.ligar()

__version__ = "2.6.9.1"


def __getattr__(name: str) -> Any:
    """Lazy-load main and Client to avoid circular imports."""
    if name == "main":
        from .cli import main

        return main
    if name == "Client":
        from .qopy import Client

        return Client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
