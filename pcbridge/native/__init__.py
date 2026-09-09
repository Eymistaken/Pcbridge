"""Private process boundary for the optional pcbridge native helper."""

from .client import NativeClient, NativeHandshake, NativeResponse
from .discovery import discover_native_binary
from .registry import NativeProcessEntry, NativeRegistry

__all__ = [
    "NativeClient",
    "NativeHandshake",
    "NativeProcessEntry",
    "NativeRegistry",
    "NativeResponse",
    "discover_native_binary",
]
