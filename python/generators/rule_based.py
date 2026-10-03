from typing import List
from events import Event
from demo_generator import generate_pattern
from .base import Generator


class RuleBasedGenerator(Generator):
    """No learning at all — deterministic pattern, useful as a baseline /
    smoke test that doesn't need a model or a network connection."""

    def generate(self, style: str = "", bars: int = 4, bpm: int = 172,
                 ticks_per_second: float = 192.0, **kwargs) -> List[Event]:
        return generate_pattern(ticks_per_second=ticks_per_second, bars=bars, bpm=bpm)
