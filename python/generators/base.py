"""
generators/base.py — the one interface every backend implements.

The engine (sequencer.Sequencer) never imports a specific backend and
never knows whether the events it's rendering came from a cloud API, a
local checkpoint, a rule-based stub, or a human/Claude typing them out by
hand. It only ever sees List[Event]. That decoupling is the actual
"plugin" property — new backends register themselves (see registry.py)
instead of being wired in by hand at every call site.
"""

from abc import ABC, abstractmethod
from typing import List
from events import Event


class Generator(ABC):
    @abstractmethod
    def generate(self, style: str, bars: int, bpm: int,
                 ticks_per_second: float = 192.0, **kwargs) -> List[Event]:
        """Return a complete, End()-terminated event sequence."""
        raise NotImplementedError
