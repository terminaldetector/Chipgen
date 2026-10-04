"""score.py — how good is a measured sound, against what was asked.

Three things are scored and kept apart so that the one that matters can be
turned up:

  * **objective** — does the *measured* sound sit where the scenario wants
    it (a target DNA, or a box per axis: "brightness 0.6-0.9, metallicity
    over 0.5")? This is the point of the run.
  * **fidelity** — does the instrument sound like its own DNA? An
    instrument that was asked to be dark and is not has a compiler problem,
    whatever the scenario wanted.
  * **novelty** — how far is it from everything already kept? Without this
    a search keeps forty copies of its best answer.

None of it uses a language model; it is a weighted distance in the space
descriptors.py measures.
"""

import math
from typing import Dict, Iterable, Optional

from .dna import AXES, DNA, _canonical

#: How much each axis counts. Detune and motion are read from a note about
#: a second long and are the least reliable, so they count for less.
DEFAULT_WEIGHTS = {
    "brightness": 1.0, "attack": 1.0, "body": 1.0, "decay": 0.9,
    "metallicity": 0.8, "detune": 0.5, "motion": 0.5, "bass_weight": 0.9,
    "articulation": 0.8, "noise": 0.7, "punch": 0.7,
}


class Objective:
    """What a scenario wants, in the same axes the DNA uses.

    `target` is where the sound should sit; `box` replaces the point by a
    range on any axis ({"metallicity": (0.5, 1.0)}); `weights` override the
    defaults; axes in `ignore` are not scored at all.

    A target written as a few axes ({"brightness": 0.7, "motion": 0.4})
    means *only those*: the axes it does not name are free, because a
    scenario that says nothing about decay does not want decay pulled to
    0.5. A whole DNA (what a search seeded from existing sounds uses) names
    every axis.
    """

    def __init__(self, target=None, box: Optional[dict] = None,
                 weights: Optional[dict] = None, ignore: Iterable[str] = ()):
        self.ignore = set(ignore)
        self._said_ignore = set(ignore)           # what the file said
        self.box = {a: (float(lo), float(hi))
                    for a, (lo, hi) in (box or {}).items()}
        for axis in self.box:
            if axis not in AXES:
                raise ValueError(f"{axis!r} is not a DNA axis")
        if isinstance(target, dict):
            self.target = DNA.from_dict(target)       # names the bad axis
            named = {_canonical(k) for k in target}
            self.ignore |= set(AXES) - named - set(self.box)
        else:
            self.target = target or DNA()
        self.weights = dict(DEFAULT_WEIGHTS)
        self.weights.update(weights or {})

    def targeted(self):
        return [a for a in AXES if a not in self.ignore]

    def error(self, axis: str, measured: float) -> float:
        if axis in self.box:
            lo, hi = self.box[axis]
            return max(0.0, lo - measured, measured - hi)
        return abs(getattr(self.target, axis) - measured)

    def to_dict(self):
        return {"target": {a: round(getattr(self.target, a), 4)
                           for a in AXES
                           if a not in self.ignore and a not in self.box},
                "box": {a: list(r) for a, r in self.box.items()},
                "weights": {a: w for a, w in self.weights.items()
                            if w != DEFAULT_WEIGHTS.get(a)},
                "ignore": sorted(self._said_ignore)}

    @classmethod
    def from_dict(cls, data: dict) -> "Objective":
        data = data or {}
        ignore = set(data.get("ignore", ()))
        target = data.get("target")
        if target is not None and not isinstance(target, dict):
            raise ValueError("objective.target is an object like "
                             "{\"brightness\": 0.7}")
        return cls(dict(target) if target is not None else None,
                   {a: tuple(r) for a, r in data.get("box", {}).items()},
                   data.get("weights"), ignore)


def distance(a: Dict[str, float], b: Dict[str, float],
             axes: Iterable[str] = AXES,
             weights: Optional[dict] = None) -> float:
    """Weighted RMS distance between two measured profiles, 0..1."""
    total = norm = 0.0
    for axis in axes:
        x, y = a.get(axis), b.get(axis)
        if x is None or y is None:
            continue
        w = (weights or DEFAULT_WEIGHTS).get(axis, 1.0)
        total += w * (x - y) ** 2
        norm += w
    return math.sqrt(total / norm) if norm else 0.0


def fit_to(target_error_fn, measured: Dict[str, Optional[float]],
           supports: Iterable[str], weights: Dict[str, float],
           ignore: Iterable[str] = ()):
    """-> (fit 0..1, {axis: error}). 1.0 is every axis on target."""
    ignore = set(ignore)
    total = norm = 0.0
    errors = {}
    for axis in supports:
        value = measured.get(axis)
        if value is None or axis in ignore:
            continue
        w = weights.get(axis, 1.0)
        if w <= 0:
            continue
        e = target_error_fn(axis, value)
        errors[axis] = e
        total += w * e * e
        norm += w
    if not norm:
        return 0.0, errors
    return max(0.0, 1.0 - math.sqrt(total / norm) * 1.6), errors


def objective_fit(objective: Objective, measured, supports):
    return fit_to(objective.error, measured, supports, objective.weights,
                  objective.ignore)


def fidelity(dna: DNA, measured, supports, weights=None):
    return fit_to(lambda axis, value: abs(getattr(dna, axis) - value),
                  measured, supports, weights or DEFAULT_WEIGHTS)


def novelty(measured, archive_profiles, k: int = 3) -> float:
    """Mean distance to the k nearest kept sounds, scaled so that 0.25 (a
    quarter of the range, on average over the axes) counts as fully new."""
    if not archive_profiles:
        return 1.0
    nearest = sorted(distance(measured, other) for other in archive_profiles)
    near = nearest[:k]
    return min(1.0, (sum(near) / len(near)) / 0.25)


def quality(issues) -> float:
    """1.0 for a clean render; each warning costs a little; an error ends it."""
    if any(i.level == "error" for i in issues):
        return 0.0
    return max(0.0, 1.0 - 0.12 * sum(1 for i in issues if i.level == "warn"))


def combine(objective_fit_: float, fidelity_: float, novelty_: float,
            quality_: float, w_novelty: float = 0.10) -> float:
    base = 0.70 * objective_fit_ + 0.15 * fidelity_ + 0.05 * quality_
    return base + w_novelty * novelty_
