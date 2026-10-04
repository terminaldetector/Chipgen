"""registry.py — which instruments in the engine's banks came from the forge.

A forge instrument is more than the patch the engine's bank stores. It can
have a detune layer (a second channel a few cents sharp), a channel setting
(the YM2612's LFO depth, the OPL2's vibrato and tremolo depth), or, on the
NES, be nothing but macros. The engine's banks know none of that, so
`install()` also records the instrument here, and `placement.expand` reads
the record when it turns a score into events.

Nothing is registered until a bank is installed, and the tracker only looks
here if this module has been imported, so a score that never touches the
forge pays nothing.
"""

from typing import Dict, Optional, Tuple

#: (backend name, instrument name) -> backends.base.Compiled
FORGE: Dict[Tuple[str, str], object] = {}


def register(backend_name: str, compiled) -> None:
    FORGE[(backend_name, compiled.name)] = compiled


def lookup(backend_name: str, name: str) -> Optional[object]:
    return FORGE.get((backend_name, name))


def clear() -> None:
    FORGE.clear()


def needs_expansion() -> bool:
    """True when any registered instrument asks for more than a plain
    instrument select (layers, or a channel setting)."""
    return any(c.style.get("layers") or c.setup for c in FORGE.values())
