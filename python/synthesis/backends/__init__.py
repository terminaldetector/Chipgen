"""backends — one compiler per chip, DNA in, hardware parameters out.

A backend knows three things about its chip: how to turn a genome into the
parameters the chip really has (`compile`), how to play one note of the
result through the real emulator (`render`) and how to turn the same
instrument into events for a score (`events`). Everything else — measuring,
scoring, searching, mixing — is chip-blind and sits above.
"""

from .base import Backend, Compiled, get_backend, backends, register_backend

__all__ = ["Backend", "Compiled", "get_backend", "backends",
           "register_backend"]
