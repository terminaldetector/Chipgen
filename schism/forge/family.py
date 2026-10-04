"""family.py — the shape every kind of sound fills in.

Chipgen's synthesis layer has one *backend* per chip: the same DNA is
compiled to YM2612 operators, OPL2 operators or NES macros. Impulse Tracker
is one "chip" with one way of making sound, a sample, so the choice here is
not which hardware but which **way of making the sample**, and each is a
`Family`:

    tone    a loop built from a spectrum, with an intro: leads, basses,
            pads, organs, keys, brass; unison, vibrato and noise inside
    modal   damped partials, each with its own fall: plucked strings, harps,
            pianos, marimbas, vibes, bells, glass, kalimbas
    drum    layered percussion: kick, snare, hat, clap, tom, rim, cymbal...
    fx      layered one-shots: riser, impact, zap, swoosh
    layer   a stack of the above: PCM spliced by role (click, body, noise,
            sub, tail) instead of dials averaged

A family turns a `Genome` (a DNA plus a few structural choices) into a
`Patch` (samples-as-recipe plus envelopes plus instrument switches) and
knows the few things the generic search needs to ask of it: which dials it
can honestly turn, where it is played, which structural choices exist, and
how to take its hardware numbers back to a DNA.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from .dna import AXES
from .genome import Genome
from .patch import Patch


@dataclass
class Compiled:
    """What a genome becomes. `patch` is the instrument; `setup` is what the
    channel wants besides it (nothing, yet); `style` says how it is played
    (is it pitched; how long a note is held when it is auditioned); `notes`
    says in words what the compiler chose and why, so a surprising
    instrument can be understood without reading the compiler."""
    family: str
    name: str
    patch: Patch
    setup: Dict[str, Any] = field(default_factory=dict)
    style: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


class Family:
    """Override `compile` and the class attributes."""

    name = "?"
    #: one line for `forge families`
    doc = ""
    #: the axes this family can actually realise; the rest are not scored
    supports = frozenset(AXES)
    #: probe note when a genome gives none
    default_register: Tuple[str, int] = ("A", 3)
    #: structure choices the search may flip: {gene: [values]}
    genes: Dict[str, List[Any]] = {}
    #: an unpitched sound (a drum, an effect): no pitch is checked
    pitched = True
    #: seconds a note is held when this family is auditioned or measured
    quick_hold = 0.84
    full_hold = 2.4
    tail = 0.7

    def compile(self, genome: Genome) -> Compiled:
        raise NotImplementedError

    def pin(self, genome: Genome) -> Genome:
        """Resolve every `auto` structural choice against the genome's DNA
        *once*, so that the search then moves smoothly inside one structure
        instead of lurching between them as a dial crosses a threshold."""
        return genome

    def clamp_genome(self, genome: Genome) -> Genome:
        """Replace style genes the family does not know with defaults."""
        style = dict(genome.style)
        for gene, options in self.genes.items():
            if style.get(gene) not in options:
                style[gene] = options[0]
        return genome.clone(style=style)

    def plain(self, compiled: Compiled):
        """The same instrument without the things that make the level beat
        or wander (unison, vibrato), or None if it has none. The probe reads
        every dial but detune and motion from it, because two voices beating
        shift the level at whatever moment it is read."""
        return None

    def validate(self, compiled: Compiled) -> list:
        return compiled.patch.problems()

    def reference_level(self) -> float:
        """The loudness (window RMS) a calibrated instrument should have in
        the probe module."""
        return REFERENCE_LEVEL

    def set_trim(self, compiled: Compiled, steps: int):
        """Apply a level correction: `steps` of 0.75 dB, positive = quieter
        (instrument gain is applied in steps of 2/128)."""
        gain = compiled.patch.inst.get("gain", 128)
        # 128 is unity; each step down is 20*log10(1 - 2/128 * k)
        scale = 10.0 ** (-0.75 * steps / 20.0)
        compiled.patch.inst["gain"] = max(2, min(128, int(round(gain * scale
                                                                / 2.0)) * 2))

    def to_dict(self, compiled: Compiled) -> dict:
        return {"name": compiled.name, "patch": compiled.patch.to_dict(),
                "style": dict(compiled.style), "setup": dict(compiled.setup),
                "notes": list(compiled.notes)}

    def from_dict(self, data: dict) -> Compiled:
        return Compiled(self.name, data.get("name", ""),
                        Patch.from_dict(data["patch"]),
                        dict(data.get("setup", {})),
                        dict(data.get("style", {})),
                        list(data.get("notes", [])))


#: The loudness of a calibrated instrument: RMS of the loudest 120 ms of the
#: probe note at full velocity in a module with mix volume 48 (the score
#: compiler's default). Measured, not chosen: samples are stored at 90% of
#: full scale and an instrument's gain can only turn them down, so the level
#: has to be one that a sustained saw lead (about 0.06) can come down to and a
#: dying pluck (about 0.02) is not far under.
REFERENCE_LEVEL = 0.030

_REGISTRY: Dict[str, Family] = {}
_ALIASES = {"lead": "tone", "bass": "tone", "pad": "tone", "synth": "tone",
            "pluck": "modal", "string": "modal", "bell": "modal",
            "metal": "modal", "keys": "modal", "mallet": "modal",
            "percussion": "drum", "perc": "drum", "drums": "drum",
            "kit": "drum", "sfx": "fx", "effect": "fx", "effects": "fx"}
_loaded = False


def register_family(family: Family) -> Family:
    _REGISTRY[family.name] = family
    return family


def get_family(name: str) -> Family:
    _load_builtin()
    key = _ALIASES.get(name.lower(), name.lower())
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(f"no family {name!r}; have: "
                       f"{', '.join(sorted(_REGISTRY))}") from None


def families() -> List[str]:
    _load_builtin()
    return sorted(_REGISTRY)


def _load_builtin():
    global _loaded
    if _loaded:
        return
    _loaded = True
    import importlib
    for module in ("tone", "modal", "drum", "fx", "layer", "fm"):
        try:
            importlib.import_module(f"{__package__}.fam_{module}")
        except ModuleNotFoundError as error:
            # a family that is not written yet is absent, not an error; one
            # that fails to import for a real reason is
            if error.name != f"{__package__}.fam_{module}":
                raise
