"""evolve.py — seed, mutate, render probe, validate, score, retain.

    realise(backend, genome)   one genome made to sound like its own DNA
    Forge(backend, objective)  a population searched toward an objective

The expensive thing in this package is a render, so the structure is built
around not spending them:

* **Realisation is a controller, not a search.** A genome whose DNA asks
  for brightness 0.7 and gets 0.55 does not need a mutation; it needs to
  be compiled as if it had asked for 0.85. `realise` reads the dials back
  after each render and moves a per-axis bias toward the error (integral
  control), a few renders at most, and keeps the best.
* **Mutation is in DNA space.** A child moves one to three dials of its
  parent, or flips one structural choice; it inherits the parent's
  biases, so it usually lands near its target on the first render. Nobody
  sweeps registers.
* **Renders are cached** by what they compiled to, so the same instrument
  reached twice costs once; and so are the samples, since a change to a
  dial that only moves the envelope does not need the samples made again.
* **Only finalists get the long probe.** Detune and motion need a note
  held for 2.4 s; the loop reads everything else from 0.84 s.

The loop never calls a model. A `Director` (director.py) may be handed in
to suggest directions, and it is asked at most once per generation.
"""

import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import dna as dna_mod, probe as probe_mod, score as score_mod
from . import validate as validate_mod
from .dna import AXES, DNA
from .family import get_family
from .genome import Genome

#: axes the controller corrects (detune and motion only when the probe
#: was long enough to measure them)
CONTROLLED = ("brightness", "attack", "body", "decay", "bass_weight",
              "metallicity", "articulation", "punch", "noise")
MOVEMENT_AXES = ("detune", "motion")
BIAS_LIMIT = 0.7


@dataclass
class Candidate:
    genome: Genome
    compiled: object
    result: probe_mod.ProbeResult
    score: float = 0.0
    parts: Dict[str, float] = field(default_factory=dict)
    errors: Dict[str, float] = field(default_factory=dict)
    generation: int = 0

    @property
    def measured(self) -> Dict[str, Optional[float]]:
        return self.result.measurement.axes

    @property
    def ok(self) -> bool:
        return self.result.ok


# --------------------------------------------------------------------------
# Realisation
# --------------------------------------------------------------------------
def _axis_errors(dna: DNA, measured, axes):
    out = {}
    for axis in axes:
        value = measured.get(axis)
        if value is None:
            continue
        out[axis] = getattr(dna, axis) - value
    return out


def realise(family, genome: Genome, probes: int = 3, tol: float = 0.05,
            full: bool = False, gain: float = 0.85, level: bool = True):
    """Compile, play, read the dials, correct the bias, repeat.

    -> (genome with its learned bias, Compiled, ProbeResult). The best of
    the attempts is returned, not the last: a correction can overshoot and
    the first render is occasionally the closest.
    """
    if isinstance(family, str):
        family = get_family(family)
    g = family.pin(genome)
    axes = [a for a in CONTROLLED if a in family.supports]
    if full:
        axes += [a for a in MOVEMENT_AXES if a in family.supports]
    best = None
    history: Dict[str, list] = {}           # axis -> [(bias used, measured)]
    for attempt in range(max(1, probes)):
        compiled = family.compile(g)
        result = probe_mod.probe(compiled, g, full=full)
        if not result.measurement.measured:
            if best is None:
                best = (9.0, g, compiled, result)
            break
        errs = _axis_errors(g.dna, result.axes, axes)
        weights = score_mod.DEFAULT_WEIGHTS
        norm = sum(weights.get(a, 1.0) for a in errs) or 1.0
        rms = math.sqrt(sum(weights.get(a, 1.0) * e * e
                            for a, e in errs.items()) / norm)
        if best is None or rms < best[0] - 1e-9:
            best = (rms, g, compiled, result)
        if rms <= tol:
            break
        bias = dict(g.bias)
        for axis, e in errs.items():
            used = bias.get(axis, 0.0)
            measured = getattr(g.dna, axis) - e
            history.setdefault(axis, []).append((used, measured))
            bias[axis] = max(-BIAS_LIMIT, min(BIAS_LIMIT, _next_bias(
                history[axis], getattr(g.dna, axis), used, gain)))
        g = g.clone(bias=bias)
    _, g, compiled, result = best
    if level:
        auto_level(family, compiled, result)
    return g, compiled, result


def _next_bias(history, target: float, current: float, gain: float) -> float:
    """The bias to try next for one axis.

    The first correction steps by the error (integral control). After
    that the response itself is known: with two readings the slope of
    measured against bias gives the step that should land on the target
    (secant method), which is what makes three renders enough for a dial
    whose gain is not 1. Once the readings straddle the target,
    interpolate between the nearest on each side instead: a compiler that
    switches structure partway (a waveform, a duty) answers with a step,
    and a controller that only steps bounces from one side to the other;
    interpolation closes in on the edge and the better side wins.
    """
    below = [(b, m) for b, m in history if m < target - 1e-9]
    above = [(b, m) for b, m in history if m > target + 1e-9]
    if below and above:
        b0, m0 = max(below, key=lambda p: p[1])
        b1, m1 = min(above, key=lambda p: p[1])
        if abs(b1 - b0) > 1e-6 and m1 > m0:
            return b0 + (target - m0) / (m1 - m0) * (b1 - b0)
    if len(history) >= 2:
        (b0, m0), (b1, m1) = history[-2], history[-1]
        if abs(b1 - b0) > 1e-3 and (m1 - m0) / (b1 - b0) > 0.2:
            slope = (m1 - m0) / (b1 - b0)
            return b1 + max(-0.35, min(0.35, (target - m1) / slope))
        if abs(b1 - b0) > 0.05 and (m1 - m0) / (b1 - b0) < 0.1:
            # pushing did nothing: this chip cannot move the dial from here,
            # and a bias that does nothing here may move something else
            return history[0][0]
    last_measured = history[-1][1]
    return current + gain * (target - last_measured)


def auto_level(family, compiled, result) -> float:
    """Set the instrument's gain so it plays at the reference loudness.
    -> the correction in dB (positive = it was louder than the reference).

    Samples are stored at 90% of full scale and gain can only turn them
    down, so an instrument quieter than the reference stays at full gain
    and is reported `soft` rather than left looking calibrated."""
    reference = family.reference_level()
    if reference <= 0 or result.level <= 0:
        return 0.0
    gain = compiled.patch.inst.get("gain", 128)
    wanted = gain * reference / result.level
    new = max(2, min(128, int(round(wanted / 2.0)) * 2))
    compiled.patch.inst["gain"] = new
    delta = 20.0 * math.log10(result.level / reference)
    # the clip warning was raised before the trim: take it back if the
    # trim has answered it
    peak = result.measurement.raw.get("peak", 0.0)
    if new < gain and peak * new / gain < 0.95:
        result.issues[:] = [i for i in result.issues if i.code != "clips"]
    if wanted > 128.5 * 1.6:
        result.issues.append(validate_mod.Issue(
            "soft", "warn",
            f"{-delta:.1f} dB under the reference level even at full gain",
            "make the sound louder before it is stored: a shorter fall, a "
            "fuller spectrum"))
    return delta


# --------------------------------------------------------------------------
# Archive
# --------------------------------------------------------------------------
class Archive:
    """The best `size` candidates that are not copies of each other.

    A newcomer within `min_distance` of a member (in measured-dial space)
    replaces it only by being better; otherwise it takes a free place or
    displaces the worst member. The archive is what a run returns and what
    novelty is measured against.
    """

    def __init__(self, size: int = 12, min_distance: float = 0.10):
        self.size = size
        self.min_distance = min_distance
        self.members: List[Candidate] = []

    def profiles(self):
        return [m.measured for m in self.members]

    def offer(self, cand: Candidate) -> bool:
        if not cand.ok:
            return False
        close = None
        for member in self.members:
            d = score_mod.distance(cand.measured, member.measured)
            if d < self.min_distance and (close is None or d < close[0]):
                close = (d, member)
        if close is not None:
            if cand.score > close[1].score + 1e-9:
                self.members[self.members.index(close[1])] = cand
                self._sort()
                return True
            return False
        if len(self.members) < self.size:
            self.members.append(cand)
            self._sort()
            return True
        worst = self.members[-1]
        if cand.score > worst.score + 1e-9:
            self.members[-1] = cand
            self._sort()
            return True
        return False

    def _sort(self):
        self.members.sort(key=lambda c: -c.score)

    def best(self) -> Optional[Candidate]:
        return self.members[0] if self.members else None


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------
class Forge:
    def __init__(self, family, objective: Optional[score_mod.Objective] = None,
                 seed: int = 0, archive_size: int = 12,
                 min_distance: float = 0.10, register=None,
                 locked=(), realise_probes: int = 2, w_novelty: float = 0.10):
        self.family = get_family(family) if isinstance(family, str) \
            else family
        self.objective = objective or score_mod.Objective()
        self.rng = random.Random(seed)
        self.seed_value = seed
        self.archive = Archive(archive_size, min_distance)
        self.register = tuple(register) if register else None
        self.locked = set(locked)
        self.realise_probes = realise_probes
        self.w_novelty = w_novelty
        self.generation = 0
        self.probes_spent = 0
        self.evaluated = 0
        self.rejected = 0
        self.history: List[dict] = []
        self._counter = 0

    # -- naming ------------------------------------------------------------
    def _name(self, base: str = "") -> str:
        self._counter += 1
        return f"{base or self.family.name}_{self.seed_value}_{self._counter:03d}"

    # -- evaluation ----------------------------------------------------------
    def evaluate(self, genome: Genome, full: bool = False,
                 generation: int = 0) -> Candidate:
        before = len(probe_mod._CACHE)
        genome = self.family.pin(genome.clone(
            name=genome.name or self._name()))
        # detune and motion can only be read from a long note
        full = full or genome.dna.detune > 0.05 or genome.dna.motion > 0.05
        if self.register and not genome.register:
            genome = genome.clone(register=self.register)
        g, compiled, result = realise(self.family, genome,
                                      probes=self.realise_probes, full=full)
        self.probes_spent += max(1, len(probe_mod._CACHE) - before)
        self.evaluated += 1
        measured = result.axes
        supports = self.family.supports
        obj_fit, errors = score_mod.objective_fit(self.objective, measured,
                                                  supports)
        fid, _ = score_mod.fidelity(g.dna, measured, supports)
        nov = score_mod.novelty(measured, self.archive.profiles())
        qual = score_mod.quality(result.issues)
        total = score_mod.combine(obj_fit, fid, nov, qual, self.w_novelty)
        if not result.ok:
            total = 0.0
        return Candidate(g, compiled, result, total,
                         {"objective": obj_fit, "fidelity": fid,
                          "novelty": nov, "quality": qual}, errors,
                         generation)

    # -- operators -----------------------------------------------------------
    def _gaps(self, cand: Optional[Candidate]) -> Dict[str, float]:
        """Signed distance from where the parent measured to where the
        objective wants it, for the axes the objective scores and the
        parent is more than a few hundredths from."""
        if cand is None:
            return {}
        out = {}
        for axis in self.objective.targeted():
            value = cand.measured.get(axis)
            if value is None or axis in self.locked:
                continue
            if axis in self.objective.box:
                lo, hi = self.objective.box[axis]
                gap = lo - value if value < lo else hi - value \
                    if value > hi else 0.0
            else:
                gap = getattr(self.objective.target, axis) - value
            if abs(gap) > 0.03:
                out[axis] = gap
        return out

    def mutate(self, parent: Genome, sigma: float, directions=None,
               parent_cand: Optional[Candidate] = None) -> Genome:
        rng = self.rng
        roll = rng.random()
        child_dna = parent.dna
        style = dict(parent.style)
        op = "jitter"
        gaps = self._gaps(parent_cand)
        if directions and roll < 0.30:
            child_dna = dna_mod.apply_directions(parent.dna, directions)
            op = "directed"
        elif gaps and roll < 0.55:
            child_dna = dna_mod.mutate(parent.dna, rng, sigma, count=(1, 2),
                                       axes=sorted(gaps), locked=self.locked,
                                       drift=gaps)
            op = "toward"
        elif roll < 0.82 or not self.family.genes:
            child_dna = dna_mod.mutate(parent.dna, rng, sigma,
                                       locked=self.locked)
        else:
            gene = rng.choice(sorted(self.family.genes))
            options = [v for v in self.family.genes[gene]
                       if v != style.get(gene) and v != "auto"]
            if options:
                style[gene] = rng.choice(options)
                op = f"style:{gene}"
        delta = ", ".join(
            f"{a}{getattr(child_dna, a) - getattr(parent.dna, a):+.2f}"
            for a in AXES
            if abs(getattr(child_dna, a) - getattr(parent.dna, a)) > 0.005)
        return parent.clone(dna=child_dna, style=style, name="",
                            lineage=parent.lineage[-6:]
                            + [f"{op} {delta}".strip()])

    def cross(self, a: Genome, b: Genome) -> Genome:
        child = dna_mod.crossover(a.dna, b.dna, self.rng)
        pick = a if self.rng.random() < 0.5 else b
        bias = {k: (a.bias.get(k, 0.0) + b.bias.get(k, 0.0)) / 2.0
                for k in sorted(set(a.bias) | set(b.bias))}
        return pick.clone(dna=child, bias=bias, name="",
                          lineage=[f"cross {a.name or '?'} x {b.name or '?'}"])

    def _pick_parent(self) -> Candidate:
        members = self.archive.members
        if self.rng.random() < 0.12 or len(members) < 3:
            return self.rng.choice(members)
        pool = self.rng.sample(members, min(3, len(members)))
        return max(pool, key=lambda c: c.score)

    # -- running ----------------------------------------------------------------
    def seed(self, genomes) -> List[Candidate]:
        out = []
        for g in genomes:
            cand = self.evaluate(g, generation=0)
            self.archive.offer(cand)
            out.append(cand)
        return out

    def run(self, generations: int = 6, offspring: int = 8,
            max_probes: Optional[int] = None, seconds: Optional[float] = None,
            director=None, progress: Optional[Callable] = None,
            sigma: float = 0.18):
        started = time.time()
        if not self.archive.members:
            raise ValueError("nothing to evolve from: seed() the forge first")
        for gen in range(1, generations + 1):
            self.generation += 1
            directions = None
            if director is not None:
                try:
                    directions = director.directions(self.summary())
                except Exception as error:         # a model must never end a run
                    self.history.append({"generation": self.generation,
                                         "director_error": str(error)})
            step_sigma = sigma * (1.0 - 0.6 * (gen - 1) / max(1, generations))
            made = kept = 0
            for _ in range(offspring):
                if max_probes is not None and self.probes_spent >= max_probes:
                    break
                if seconds is not None and time.time() - started > seconds:
                    break
                members = self.archive.members
                if len(members) >= 2 and self.rng.random() < 0.2:
                    a, b = self.rng.sample(members, 2)
                    child = self.cross(a.genome, b.genome)
                else:
                    parent = self._pick_parent()
                    child = self.mutate(parent.genome, step_sigma, directions,
                                        parent)
                cand = self.evaluate(child, generation=self.generation)
                made += 1
                if cand.ok:
                    if self.archive.offer(cand):
                        kept += 1
                else:
                    self.rejected += 1
            best = self.archive.best()
            row = {"generation": self.generation, "made": made, "kept": kept,
                   "best": round(best.score, 4) if best else None,
                   "archive": len(self.archive.members),
                   "probes": self.probes_spent,
                   "seconds": round(time.time() - started, 2)}
            self.history.append(row)
            if progress:
                progress(row)
            if (max_probes is not None and self.probes_spent >= max_probes) \
                    or (seconds is not None and time.time() - started > seconds):
                break
        return self.archive

    def finalize(self, top: int = 6) -> List[Candidate]:
        """Re-probe the best with the long note and rescore them, so
        detune and motion are read for the instruments that are kept."""
        out = []
        for cand in self.archive.members[:top]:
            again = self.evaluate(cand.genome.clone(name=cand.genome.name),
                                  full=True, generation=cand.generation)
            out.append(again if again.ok else cand)
        out.sort(key=lambda c: -c.score)
        return out

    def summary(self) -> dict:
        """What a Director is shown: the archive as dials, compactly."""
        rows = []
        for c in self.archive.members:
            rows.append({"name": c.genome.name, "score": round(c.score, 3),
                         "dna": c.genome.dna.to_dict(2),
                         "measured": {k: (None if v is None else round(v, 2))
                                      for k, v in c.measured.items()},
                         "errors": {k: round(v, 2)
                                    for k, v in c.errors.items()}})
        return {"family": self.family.name,
                "objective": self.objective.to_dict(),
                "generation": self.generation, "members": rows}
