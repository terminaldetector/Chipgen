from typing import List

from events import Event
from cloud_generator import DEFAULT_MODEL, generate_pattern_cloud
from .base import Generator


class CloudGenerator(Generator):
    """Synchronous call to a cloud LLM (see cloud_generator.py, currently
    wired to the Anthropic API in _call_model()). Swapping providers means
    editing that one function — this class, the registry entry, and
    everything downstream (Sequencer) stay untouched.

    `fmt` picks what the model is asked to emit: "tracker" (default) is a
    compact grid, cheaper in tokens and easier for a model to keep
    rhythmically straight; "json" is the flat event array, better for
    models with strong structured-output modes.
    """

    def __init__(self, model: str = DEFAULT_MODEL, fmt: str = "tracker"):
        self.model = model
        self.fmt = fmt

    def generate(self, style: str, bars: int = 4, bpm: int = 172,
                 ticks_per_second: float = 192.0, **kwargs) -> List[Event]:
        return generate_pattern_cloud(style=style, bars=bars, bpm=bpm,
                                      ticks_per_second=ticks_per_second,
                                      model=self.model, fmt=self.fmt)
