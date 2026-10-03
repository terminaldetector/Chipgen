"""
generators/local_model.py — wiring for a genuinely local network (PyTorch/
ONNX/GGUF/whatever) running in-process, as opposed to rule_based.py (no
learning) or cloud.py (network call). Nothing here is trained — there's
no chiptune-event training set shipping in this project. This is the
socket a real model plugs into, honestly left unplugged.

To make this real:
  1. Tokenize Event streams (events.py already gives you .to_dict(), so
     JSON-per-event or a fixed-width categorical encoding both work).
  2. Train something autoregressive on a corpus of such token streams
     (a small transformer is plenty for 6 FM channels + 4 PSG channels
     worth of vocabulary).
  3. Load the checkpoint in _load() and sample tokens -> Event objects
     in generate() until you emit End() or hit a step budget.
"""

from typing import List
from events import Event
from .base import Generator


class LocalModelGenerator(Generator):
    def __init__(self, checkpoint_path: str = None):
        self.model = None
        if checkpoint_path:
            self._load(checkpoint_path)

    def _load(self, checkpoint_path: str):
        raise NotImplementedError(
            "load your own checkpoint here, e.g.:\n"
            "  import torch; self.model = torch.load(checkpoint_path); self.model.eval()\n"
            "or onnxruntime.InferenceSession(...), or a llama.cpp GGUF handle — "
            "whatever inference stack you're already using elsewhere is fine, "
            "the only contract is that generate() below returns List[Event]."
        )

    def generate(self, style: str, bars: int = 4, bpm: int = 172,
                 ticks_per_second: float = 192.0, **kwargs) -> List[Event]:
        if self.model is None:
            raise RuntimeError(
                "LocalModelGenerator has no checkpoint loaded — it's a wiring "
                "stub, not a trained model. Use RuleBasedGenerator or "
                "CloudGenerator meanwhile, or train something against "
                "events.py's vocabulary and pass checkpoint_path=..."
            )
        # inference loop goes here: condition on style/bars/bpm, sample
        # Event tokens autoregressively until End() or a step budget —
        # identical shape to any symbolic-music transformer setup.
        raise NotImplementedError
