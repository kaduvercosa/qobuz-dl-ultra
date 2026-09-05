__version__ = "2.5.2.1"


def __getattr__(name):
    if name == "main":
        from .cli import main

        return main
    if name == "Client":
        from .qopy import Client

        return Client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
