# Copyright: (c) 2026, Jordan Borean (@jborean93) <jborean93@gmail.com>
# MIT License (see LICENSE or https://opensource.org/licenses/MIT)

"""Builds and operates alai indexes as a tree of files on disk, leaving how they're published to the caller. The
command line is the interface, and every module is private."""


def __getattr__(name: str) -> str:
    # Looked up and cached on first use.
    if name == "__version__":
        from importlib.metadata import version

        value = version("alai-index")
        globals()["__version__"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
