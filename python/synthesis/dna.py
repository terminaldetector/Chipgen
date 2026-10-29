"""dna.py — Instrument DNA: the dials, and what each one means in numbers.

A patch is a few dozen hardware registers and no two chips agree about
what they are. A *sound* is something a person can describe: darker,
snappier, more metallic, thicker. The DNA is that description, with one
rule that keeps it honest: **every axis is defined by a measurement**.
"Brightness 0.7" is not a mood, it is "the spectral centroid sits at
about 2^(0.7*4) = 7x the fundamental", and `descriptors.py` reads that
back off a rendered note. So the same DNA asked of the YM2612, the OPL2
and the NES means the same thing, and a compiler that gets it wrong shows
up as a number and not as an argument.

All axes run 0..1. They are the nine the brief asked for plus two that
percussion cannot do without:

    brightness   dark, near a sine .. harsh, buzzy
    attack       instant .. a slow swell (log time, 0 = no rise, 1 = 400 ms)
    body         dies away .. holds at full level (the level 0.6 s in, in dB)
    decay        short ring .. very long (log time, how long the fall takes)
    metallicity  smooth, harmonic .. clangorous, hollow, inharmonic
    detune       one pitch .. a wide ensemble (slow beating between voices)
    motion       static .. strong vibrato, tremolo or sweep (3.5 Hz and up)
    bass_weight  thin .. sub-heavy (fundamental against its next harmonics)
    articulation staccato, gated .. legato, ringing out after note-off
    noise        tonal .. noise (extra)
    punch        none .. strong attack transient, click or thump (extra)

The exact rule for each is `AXIS_DOC` below, and the code that applies it is
`descriptors.measure`; a test keeps the two from drifting apart.
"""

import math
import random
from dataclasses import dataclass, fields, replace
from typing import Dict, Iterable, Optional, Tuple

CORE_AXES = ("brightness", "attack", "body", "decay", "metallicity",
             "detune", "motion", "bass_weight", "articulation")
EXTRA_AXES = ("noise", "punch")
AXES = CORE_AXES + EXTRA_AXES

#: axis -> (what 0 sounds like, what 1 sounds like, how it is measured)
AXIS_DOC = {
    "brightness": ("dark, close to a sine", "harsh and buzzy",
                   "spectral centroid / fundamental, 1x -> 0, 16x -> 1 (log)"),
    "attack": ("instant", "a slow swell",
               "10%-90% rise time of the loudness envelope, "
               "log(1 + t / 2 ms): 0 ms -> 0, 400 ms -> 1"),
    "body": ("dies away", "holds at full level",
             "level 0.6 s into the note (0.25 s after the peak for a slow "
             "swell) against the peak, -40 dB -> 0, 0 dB -> 1"),
    "decay": ("a short ring", "rings for seconds",
              "time for the level to fall 63% of the way from the peak to "
              "the body, 30 ms -> 0, 3 s -> 1 (log); 1 when it hardly falls"),
    "metallicity": ("smooth, harmonic", "clangorous, hollow, inharmonic",
                    "inharmonicity of the partials plus roughness among the "
                    "upper ones"),
    "detune": ("one pitch", "a wide ensemble",
               "depth of the slow (0.7-3.5 Hz) amplitude beating of the "
               "lowest strong partial, read from a second or more of "
               "steady note: 4% -> 0, 44% -> 1"),
    "motion": ("static", "strong vibrato, tremolo or sweep",
               "amplitude and pitch movement of the held note at 3.5 Hz "
               "and up"),
    "bass_weight": ("thin", "sub-heavy",
                    "energy at or under 1.5x the fundamental against "
                    "1.5-4.5x, 0.2 -> 0, 100 -> 1 (log)"),
    "articulation": ("staccato, gated", "legato, rings out after note-off",
                     "time to fall 20 dB after note-off, 20 ms -> 0, "
                     "1 s -> 1 (log); not measured when the note had "
                     "already died"),
    "noise": ("tonal", "noise", "spectral flatness of the held note, "
              "0.005 -> 0, 0.5 -> 1 (log)"),
    "punch": ("none", "a strong attack transient",
              "how many octaves higher the spectral centroid of the attack "
              "is than that of the body, 3 octaves -> 1"),
}

#: Words for a value, for describe() and for prompts. Five steps is what a
#: person can tell apart and a model can use.
_WORDS = ("very low", "low", "middling", "high", "very high")


class DNAError(ValueError):
    """A bad axis name or value. The message says which axes exist."""


def _clip(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else float(value)


@dataclass(frozen=True)
class DNA:
    brightness: float = 0.5
    attack: float = 0.1
    body: float = 0.5
    decay: float = 0.5
    metallicity: float = 0.2
    detune: float = 0.0
    motion: float = 0.0
    bass_weight: float = 0.4
    articulation: float = 0.5
    noise: float = 0.0
    punch: float = 0.0

    def __post_init__(self):
        for axis in AXES:
            value = getattr(self, axis)
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or value != value:
                raise DNAError(f"{axis} must be a number from 0 to 1, "
                               f"got {value!r}")
            if not 0.0 <= value <= 1.0:
                raise DNAError(f"{axis} must be between 0 and 1, got "
                               f"{value:g}. Use DNA.clamped() to saturate.")

    # -- construction --------------------------------------------------
    @classmethod
    def clamped(cls, **values) -> "DNA":
        """Like DNA(...) but saturating out-of-range values instead of
        refusing them, which is what a mutation wants."""
        _check_axes(values)
        return cls(**{axis: _clip(v) for axis, v in values.items()})

    @classmethod
    def from_dict(cls, data: dict) -> "DNA":
        """Tolerant reader: axis names in any case, with spaces, hyphens
        or the `bass` / `art` shorthands a model is likely to type."""
        values = {}
        for key, value in dict(data).items():
            axis = _canonical(key)
            if axis is None:
                raise DNAError(
                    f"{key!r} is not a DNA axis; the axes are "
                    f"{', '.join(AXES)}")
            values[axis] = value
        return cls(**values)

    def to_dict(self, round_to: int = 4) -> Dict[str, float]:
        return {axis: round(getattr(self, axis), round_to) for axis in AXES}

    # -- arithmetic ----------------------------------------------------
    def vector(self, axes: Iterable[str] = AXES) -> Tuple[float, ...]:
        return tuple(getattr(self, a) for a in axes)

    def with_(self, **changes) -> "DNA":
        _check_axes(changes)
        return replace(self, **{a: _clip(v) for a, v in changes.items()})

    def shifted(self, deltas: Dict[str, float]) -> "DNA":
        """Add per-axis deltas, saturating."""
        _check_axes(deltas)
        return replace(self, **{a: _clip(getattr(self, a) + d)
                                for a, d in deltas.items()})

    def distance(self, other: "DNA", weights: Optional[dict] = None,
                 axes: Iterable[str] = AXES) -> float:
        """Weighted root-mean-square difference, 0 (same) .. 1."""
        total = norm = 0.0
        for axis in axes:
            weight = 1.0 if not weights else float(weights.get(axis, 1.0))
            if weight <= 0:
                continue
            diff = getattr(self, axis) - getattr(other, axis)
            total += weight * diff * diff
            norm += weight
        return math.sqrt(total / norm) if norm else 0.0

    def describe(self, threshold: float = 0.0) -> str:
        """`bright (0.8), instant attack (0.0) ...` — for people and prompts.

        Axes within `threshold` of the neutral default are left out when a
        threshold is given.
        """
        parts = []
        default = DNA()
        for axis in AXES:
            value = getattr(self, axis)
            if threshold and abs(value - getattr(default, axis)) < threshold:
                continue
            parts.append(f"{axis} {_word(value)} ({value:.2f})")
        return ", ".join(parts)

    def __str__(self):
        return "DNA(" + ", ".join(f"{a}={getattr(self, a):.2f}"
                                  for a in AXES) + ")"


_ALIASES = {
    "bright": "brightness", "brite": "brightness", "tone": "brightness",
    "att": "attack", "atk": "attack", "sustain": "body", "level": "body",
    "dec": "decay", "release": "articulation", "art": "articulation",
    "metal": "metallicity", "metallic": "metallicity",
    "inharmonic": "metallicity", "inharmonicity": "metallicity",
    "chorus": "detune", "width": "detune", "unison": "detune",
    "mod": "motion", "movement": "motion", "wobble": "motion",
    "bass": "bass_weight", "sub": "bass_weight", "weight": "bass_weight",
    "legato": "articulation", "hiss": "noise", "grit": "noise",
    "transient": "punch", "click": "punch", "thump": "punch",
}


def _canonical(key: str) -> Optional[str]:
    name = str(key).strip().lower().replace(" ", "_").replace("-", "_")
    if name in AXES:
        return name
    return _ALIASES.get(name.replace("_", "")) or _ALIASES.get(name)


def _check_axes(values: dict):
    for axis in values:
        if axis not in AXES:
            raise DNAError(f"{axis!r} is not a DNA axis; the axes are "
                           f"{', '.join(AXES)}")


def _word(value: float) -> str:
    return _WORDS[min(4, int(value * 5.0))]


# --------------------------------------------------------------------------
# Operators the search uses
# --------------------------------------------------------------------------
def lerp(a: DNA, b: DNA, t: float) -> DNA:
    """Straight interpolation: t=0 is `a`, t=1 is `b`."""
    t = _clip(t)
    return DNA(**{x: getattr(a, x) * (1 - t) + getattr(b, x) * t
                  for x in AXES})


def blend(parts) -> DNA:
    """Weighted mean of [(dna, weight), ...]. The recursion step of mixing:
    a blend of blends is itself a blend, so mixing trees evaluate bottom-up
    without special cases."""
    parts = [(d, float(w)) for d, w in parts if w > 0]
    if not parts:
        raise DNAError("nothing to blend")
    total = sum(w for _, w in parts)
    return DNA(**{x: _clip(sum(getattr(d, x) * w for d, w in parts) / total)
                  for x in AXES})


def crossover(a: DNA, b: DNA, rng: random.Random) -> DNA:
    """Each axis from either parent, or somewhere between: uniform
    crossover with a random mixing weight per axis, so the child is not
    stuck on the line joining its parents."""
    out = {}
    for axis in AXES:
        pick = rng.random()
        if pick < 0.4:
            out[axis] = getattr(a, axis)
        elif pick < 0.8:
            out[axis] = getattr(b, axis)
        else:
            w = rng.random()
            out[axis] = getattr(a, axis) * w + getattr(b, axis) * (1 - w)
    return DNA(**{k: _clip(v) for k, v in out.items()})


def mutate(dna: DNA, rng: random.Random, sigma: float = 0.15,
           count: Tuple[int, int] = (1, 3), axes: Iterable[str] = AXES,
           locked: Iterable[str] = ()) -> DNA:
    """Nudge a few axes, not all of them.

    Moving every axis at once turns a mutation into a random restart: a
    child is only informative about its parent if most of it still *is*
    its parent. One to three axes move, each by a Gaussian step, and a step
    that would leave the range is reflected back rather than clipped, so the
    ends of the range are not over-sampled.
    """
    pool = [a for a in axes if a not in set(locked)]
    if not pool:
        return dna
    low, high = count
    chosen = rng.sample(pool, min(len(pool), rng.randint(low, high)))
    out = {}
    for axis in chosen:
        value = getattr(dna, axis) + rng.gauss(0.0, sigma)
        if value < 0.0:
            value = -value
        if value > 1.0:
            value = 2.0 - value
        out[axis] = _clip(value)
    return replace(dna, **out)


def apply_directions(dna: DNA, directions: Iterable[dict]) -> DNA:
    """Apply what a Director asked for: [{"axis": "brightness", "add": 0.2}
    | {"axis": "attack", "set": 0.0}, ...]. Unknown axes raise, because a
    direction that silently did nothing would waste a generation."""
    out = dna
    for item in directions:
        axis = _canonical(item.get("axis", ""))
        if axis is None:
            raise DNAError(f"direction names {item.get('axis')!r}, which is "
                           f"not an axis; the axes are {', '.join(AXES)}")
        if "set" in item:
            out = out.with_(**{axis: float(item["set"])})
        elif "add" in item:
            out = out.shifted({axis: float(item["add"])})
        else:
            raise DNAError(f"direction for {axis} needs `add` or `set`")
    return out
