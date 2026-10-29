"""base.py — the shape every chip backend fills in."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..dna import DNA, AXES
from ..genome import Genome


@dataclass
class Compiled:
    """What a genome becomes on one chip.

    `patch` is the chip's own object (an opn2.FMInstrument, an
    opl2.OPLInstrument or a nes_driver.NESInstrument). `setup` is what the
    channel needs besides the patch (LFO depth, panning); `style` is how
    the driver should *play* it (gate length, whether to retrigger a tied
    note); `notes` says in words what the compiler chose and why, so a
    surprising instrument can be understood without reading the compiler.
    """
    backend: str
    name: str
    patch: Any
    setup: Dict[str, Any] = field(default_factory=dict)
    style: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


class Backend:
    """Override `compile`, `render`, `events` and the class attributes."""

    name = "?"
    chip = "?"
    #: how many of the chip's channels one note of this instrument needs
    voices_per_note = 1
    #: the axes this chip can actually realise; the rest are not scored
    supports = frozenset(AXES)
    #: probe note when a genome gives none
    default_register: Tuple[str, int] = ("A", 3)
    #: structure choices the search may flip: {gene: [values]}
    genes: Dict[str, List[Any]] = {}

    # -- the contract ----------------------------------------------------
    def compile(self, genome: Genome) -> Compiled:
        raise NotImplementedError

    def render(self, compiled: Compiled, note: str, octave: int,
               velocity: int = 127, hold: float = 1.0, tail: float = 0.5,
               ensemble: bool = True):
        """Play one note through the real emulator. -> (mono list, rate).

        `ensemble=False` plays the main voice alone, without the extra
        channels a detune layer adds: the probe reads every dial but detune
        from that, because two voices beating shift the level at any
        moment you read it and the envelope dials would follow the beat.
        """
        raise NotImplementedError

    def events(self, compiled: Compiled, channel, note: str, octave: int,
               length_ticks: int, velocity: int, ticks_per_second: float):
        """-> [(tick offset, Event)] for one note of this instrument."""
        raise NotImplementedError

    def setup_events(self, compiled: Compiled, channel):
        """-> [Event] to run once per channel before the first note."""
        return []

    def install(self, compiled: Compiled, character: str = ""):
        """Make the instrument selectable by name in this process."""
        raise NotImplementedError

    def reference_level(self) -> float:
        """The loudness a calibrated instrument of this chip should have
        (window RMS), for auto-levelling."""
        return 0.0

    def set_trim(self, compiled: Compiled, steps: int):
        """Apply a level correction in the chip's own units."""

    def validate(self, compiled: Compiled) -> list:
        """The ways an instrument's hardware numbers are not legal for the
        chip, as sentences naming the field. [] when it is fine. A bank
        written by hand (or edited, or made by a model) is checked with
        this on load, because the chip would take any number written to its
        register and play something else."""
        return []

    def to_dict(self, compiled: Compiled) -> dict:
        raise NotImplementedError

    def from_dict(self, data: dict) -> Compiled:
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------
    def pin(self, genome: Genome) -> Genome:
        """Resolve every `auto` structural choice against the genome's DNA
        *once*, so that the search then moves smoothly inside one structure
        instead of lurching between algorithms as a dial crosses a
        threshold."""
        return genome

    def clamp_genome(self, genome: Genome) -> Genome:
        """Replace style genes the chip does not know with defaults."""
        style = dict(genome.style)
        for gene, options in self.genes.items():
            if style.get(gene) not in options:
                style[gene] = options[0]
        return genome.clone(style=style)


def in_range(problems: list, label: str, value, low, high):
    """Append a sentence to `problems` unless low <= value <= high."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or value != value or not low <= value <= high:
        problems.append(f"{label} is {value!r}; the chip takes {low} to {high}")


_REGISTRY: Dict[str, Backend] = {}


def register_backend(backend: Backend):
    _REGISTRY[backend.name] = backend
    return backend


def get_backend(name: str) -> Backend:
    _load_builtin()
    key = _ALIASES.get(name.lower(), name.lower())
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(f"no backend {name!r}; have: "
                       f"{', '.join(sorted(_REGISTRY))}") from None


def backends() -> List[str]:
    _load_builtin()
    return sorted(_REGISTRY)


_ALIASES = {"genesis": "ym2612", "md": "ym2612", "smd": "ym2612",
            "opn2": "ym2612", "fm": "ym2612", "opl": "opl2",
            "ym3812": "opl2", "adlib": "opl2", "2a03": "nes",
            "rp2a03": "nes", "famicom": "nes"}
_loaded = False


def _load_builtin():
    global _loaded
    if _loaded:
        return
    _loaded = True
    import importlib
    for module in ("ym2612", "ym3812", "rp2a03"):
        try:
            importlib.import_module(f"{__package__}.{module}")
        except ModuleNotFoundError as error:
            # a backend that is not written yet is absent, not an error;
            # a backend that fails to import for a real reason is one
            if error.name != f"{__package__}.{module}":
                raise
