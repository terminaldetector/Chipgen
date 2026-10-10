"""mixing.py — blend instruments into new ones, recursively, on purpose.

Two ways to mix, because there are two things worth preserving:

* **dna** — blend what the instruments *sound like*. The parents' measured
  dials are averaged (weighted), the structural choice of the heavier parent
  wins, and the result is compiled afresh and corrected by the same loop
  as everything else. It works across chips and for any two instruments,
  and it never produces nonsense; what it does not do is keep any detail of
  either parent.
* **morph** — blend the *hardware numbers*. Two FM patches with the same
  algorithm are interpolated operator by operator (levels and rates on
  their natural scales, discrete choices by weight); with different
  algorithms the heavier parent's structure hosts the lighter one's
  modulators, matched by their depth in the chain. This keeps the lumpy,
  specific character of real patches, and it can fail, so the result is
  played and checked, and falls back to `dna` if it is silent or off pitch.

A mix of mixes is a tree, evaluated from the leaves, so "two parts bass,
one part bell, then half of that with brass" is one recipe. Every node is
measured, so the depth costs one render per node and nothing is guessed.

Recipes are JSON:

    {"mix": [{"from": "slap_bass", "weight": 2},
             {"mix": [{"from": "bell_pluck"}, {"from": "brass"}],
              "weight": 1}],
     "mode": "auto"}

`from` names a bank instrument, a built-in patch, or `archetype:NAME`.
"""

import copy
import json
from typing import List, Optional, Tuple

from . import archetypes, evolve, probe as probe_mod, score as score_mod
from . import nes_driver
from .backends import get_backend
from .backends.base import Compiled
from .dna import AXES, DNA, blend, lerp
from .genome import Genome, slug

MAX_DEPTH = 6


class MixError(ValueError):
    pass


# --------------------------------------------------------------------------
# Lifting: from a patch to a place in dial space
# --------------------------------------------------------------------------
def lift(backend, compiled: Compiled, register=None, name: str = "") -> Genome:
    """Measure an instrument and describe it as a genome whose DNA is what
    it measures. This is how a hand-made patch becomes a parent."""
    reg = register or backend.default_register
    g = Genome(backend.name, DNA(), name=name or compiled.name, register=reg)
    result = probe_mod.probe(backend, compiled, g, full=True)
    dna = DNA.clamped(**{a: (result.axes.get(a) or 0.0) for a in AXES})
    return g.clone(dna=dna, lineage=[f"lift:{compiled.name}"])


def builtin_compiled(backend, name: str) -> Compiled:
    """A built-in patch by name, as a Compiled (a copy, never the bank's own
    object, because auto-levelling writes to it)."""
    if backend.name == "ym2612":
        import instruments as bank
        patch = bank.get(name).copy()
        return Compiled("ym2612", name, patch, {}, {"gate": 0.9,
                                                     "retrigger": True})
    if backend.name == "opl2":
        import opl_instruments as bank
        patch = bank.get(name).copy()
        return Compiled("opl2", name, patch, {}, {"gate": 0.9,
                                                   "retrigger": True,
                                                   "layers": []})
    patch = nes_driver.get(name).copy()
    return Compiled("nes", name, patch, {}, {"gate": 0.9, "retrigger": True,
                                              "channel": patch.channel,
                                              "pitched": patch.channel != "noise"})


def lift_named(backend, name: str) -> Genome:
    return lift(backend, builtin_compiled(backend, name), name=name)


# --------------------------------------------------------------------------
# Morphing: blend hardware numbers
# --------------------------------------------------------------------------
def _pick(a, b, t):
    return b if t >= 0.5 else a


def _lerp_int(a, b, t, lo=None, hi=None):
    v = int(round(a * (1.0 - t) + b * t))
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def morph_fm(a, b, t: float):
    """Blend two FMInstruments. t=0 is `a`, t=1 is `b`."""
    from opn2 import FMInstrument, Operator
    from .backends import ym2612

    def mix_op(x, y, w):
        return Operator(
            detune=_pick(x.detune, y.detune, w),
            multiple=_pick(x.multiple, y.multiple, w),
            total_level=_lerp_int(x.total_level, y.total_level, w, 0, 127),
            attack_rate=_lerp_int(x.attack_rate, y.attack_rate, w, 0, 31),
            decay_rate=_lerp_int(x.decay_rate, y.decay_rate, w, 0, 31),
            sustain_rate=_lerp_int(x.sustain_rate, y.sustain_rate, w, 0, 31),
            release_rate=_lerp_int(x.release_rate, y.release_rate, w, 0, 15),
            sustain_level=_lerp_int(x.sustain_level, y.sustain_level, w, 0, 15),
            ssg_eg=_pick(x.ssg_eg, y.ssg_eg, w),
            rate_scaling=_pick(x.rate_scaling, y.rate_scaling, w),
            am_enable=_pick(x.am_enable, y.am_enable, w))

    if a.algorithm == b.algorithm:
        ops = [mix_op(x, y, t) for x, y in zip(a.operators, b.operators)]
        return FMInstrument(a.algorithm, _lerp_int(a.feedback, b.feedback, t,
                                                    0, 7), ops, "",
                            _lerp_int(a.trim, b.trim, t))
    # different structures: the heavier parent hosts, the lighter one's
    # operators are matched by role and depth in the chain
    host, donor, w = (a, b, t) if t < 0.5 else (b, a, 1.0 - t)
    # `w` is the donor's share; the host keeps the rest
    host_plan = ym2612.PLANS[host.algorithm]
    donor_plan = ym2612.PLANS[donor.algorithm]
    depth_h = ym2612._depths(host_plan)
    depth_d = ym2612._depths(donor_plan)

    def ordinary(inst, number):               # op number -> Operator
        return inst.operators[[1, 3, 2, 4].index(number)]

    def ranked(plan, depth, role):
        ops = [o for o in (1, 2, 3, 4) if plan[o][0] == role]
        return sorted(ops, key=lambda o: (-depth[o], o))

    paired = {}
    for role in ("mod", "car"):
        h = ranked(host_plan, depth_h, role)
        d = ranked(donor_plan, depth_d, role)
        for i, op in enumerate(h):
            paired[op] = d[i] if i < len(d) else None
    out = {}
    for op in (1, 2, 3, 4):
        x = ordinary(host, op)
        partner = paired.get(op)
        if partner is None:
            out[op] = x.copy()
            continue
        y = ordinary(donor, partner)
        # carriers keep the host's multiples (pitch) and levels; what the
        # donor brings is its envelope; modulators bring everything
        if host_plan[op][0] == "car":
            o = x.copy()
            o.attack_rate = _lerp_int(x.attack_rate, y.attack_rate, w, 0, 31)
            o.decay_rate = _lerp_int(x.decay_rate, y.decay_rate, w, 0, 31)
            o.sustain_rate = _lerp_int(x.sustain_rate, y.sustain_rate, w, 0, 31)
            o.release_rate = _lerp_int(x.release_rate, y.release_rate, w, 0, 15)
            o.sustain_level = _lerp_int(x.sustain_level, y.sustain_level, w,
                                        0, 15)
            out[op] = o
        else:
            out[op] = mix_op(x, y, w)
    ordered = [out[1], out[3], out[2], out[4]]           # register order
    return FMInstrument(host.algorithm,
                        _lerp_int(host.feedback, donor.feedback, w, 0, 7),
                        ordered, "", host.trim)


def morph_opl(a, b, t: float):
    from opl2 import OPLInstrument, OPLOperator

    def mix_op(x, y, w):
        return OPLOperator(
            attack=_lerp_int(x.attack, y.attack, w, 0, 15),
            decay=_lerp_int(x.decay, y.decay, w, 0, 15),
            sustain_level=_lerp_int(x.sustain_level, y.sustain_level, w, 0, 15),
            release=_lerp_int(x.release, y.release, w, 0, 15),
            total_level=_lerp_int(x.total_level, y.total_level, w, 0, 63),
            key_scale_level=_pick(x.key_scale_level, y.key_scale_level, w),
            multiple=_pick(x.multiple, y.multiple, w),
            waveform=_pick(x.waveform, y.waveform, w),
            sustaining=_pick(x.sustaining, y.sustaining, w),
            key_scale_rate=_pick(x.key_scale_rate, y.key_scale_rate, w),
            tremolo=_pick(x.tremolo, y.tremolo, w),
            vibrato=_pick(x.vibrato, y.vibrato, w))
    return OPLInstrument(mix_op(a.modulator, b.modulator, t),
                         mix_op(a.carrier, b.carrier, t),
                         feedback=_lerp_int(a.feedback, b.feedback, t, 0, 7),
                         connection=_pick(a.connection, b.connection, t),
                         name="", trim=_lerp_int(a.trim, b.trim, t))


def _resample(macro, length: int):
    values = macro.values
    if len(values) == length:
        return list(values)
    if length == 1:
        return [values[0]]
    out = []
    for i in range(length):
        pos = i * (len(values) - 1) / (length - 1)
        lo = int(pos)
        hi = min(len(values) - 1, lo + 1)
        out.append(values[lo] + (values[hi] - values[lo]) * (pos - lo))
    return out


def _morph_macro(x, y, t, integer=False):
    length = max(len(x.values), len(y.values))
    xs, ys = _resample(x, length), _resample(y, length)
    values = [xs[i] * (1 - t) + ys[i] * t for i in range(length)]
    if integer:
        values = [int(round(v)) for v in values]

    def point(m, name):
        p = getattr(m, name)
        return None if p is None else p / max(1, len(m.values))
    out = nes_driver.Macro(values)
    for name in ("loop", "release"):
        pa, pb = point(x, name), point(y, name)
        if pa is None and pb is None:
            continue
        pos = pb if pa is None else pa if pb is None else \
            pa * (1 - t) + pb * t
        setattr(out, name, max(0, min(length - 1, int(round(pos * length)))))
    return nes_driver.Macro(values, out.loop, out.release)


def morph_nes(a, b, t: float):
    channel = _pick(a.channel, b.channel, t)
    inst = nes_driver.NESInstrument(
        name="", channel=channel,
        volume=_morph_macro(a.volume, b.volume, t, integer=True),
        duty=_morph_macro(a.duty, b.duty, t, integer=True),
        pitch=_morph_macro(a.pitch, b.pitch, t),
        arp=_morph_macro(a.arp, b.arp, t, integer=True),
        period=_morph_macro(a.period, b.period, t, integer=True),
        mode=_pick(a.mode, b.mode, t),
        layers=[dict(l) for l in (a.layers if t < 0.5 else b.layers)],
        trim=_lerp_int(a.trim, b.trim, t))
    return inst


MORPHS = {"ym2612": morph_fm, "opl2": morph_opl, "nes": morph_nes}


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------
class _Node:
    def __init__(self, compiled, genome, mode="", notes=None, dna_target=None):
        self.compiled = compiled
        self.genome = genome
        self.mode = mode
        self.notes = notes or []
        self.target = dna_target or genome.dna


def _leaf(backend, spec, bank, register):
    if spec.startswith("archetype:"):
        dna, reg, _, _ = archetypes.get(spec.split(":", 1)[1])
        g = Genome(backend.name, dna, name=slug(spec), register=reg)
        g, compiled, _ = evolve.realise(backend, g, probes=3, full=True)
        return _Node(compiled, g, "leaf", [f"archetype {spec}"])
    if bank is not None:
        for entry in bank.entries:
            if entry.name == spec and entry.backend == backend.name:
                compiled = backend.from_dict(backend.to_dict(entry.compiled))
                compiled.name = entry.name
                return _Node(compiled, entry.genome, "leaf",
                             [f"bank {spec}"])
    try:
        compiled = builtin_compiled(backend, spec)
    except KeyError:
        raise MixError(f"no instrument {spec!r} on {backend.name}: it is "
                       f"not in the bank being built, not a built-in patch "
                       f"and not archetype:NAME") from None
    return _Node(compiled, lift(backend, compiled, register), "leaf",
                 [f"built-in {spec}"])


def evaluate(backend, node: dict, bank=None, register=None, depth: int = 0,
             rng_seed: int = 0) -> _Node:
    if depth > MAX_DEPTH:
        raise MixError(f"a mix nests more than {MAX_DEPTH} deep")
    if "from" in node:
        return _leaf(backend, str(node["from"]), bank, register)
    parts = node.get("mix")
    if not parts or len(parts) < 2:
        raise MixError("a mix needs at least two parts")
    children = []
    for part in parts:
        child = evaluate(backend, part, bank, register, depth + 1, rng_seed)
        children.append((child, float(part.get("weight", 1.0))))
    if any(w <= 0 for _, w in children):
        raise MixError("mix weights must be positive")
    mode = node.get("mode", "auto")
    target = blend([(c.target, w) for c, w in children])
    reg = register or children[0][0].genome.register
    notes = []
    result = None
    if mode in ("morph", "auto"):
        result = _try_morph(backend, children, reg, target, notes)
    if result is None:
        g = Genome(backend.name, target,
                   style=dict(max(children, key=lambda cw: cw[1])[0]
                              .genome.style),
                   name="", register=reg,
                   lineage=["mix:dna " + " + ".join(
                       f"{c.genome.name or '?'}x{w:g}" for c, w in children)])
        g, compiled, _ = evolve.realise(backend, g, probes=3, full=True)
        notes.append("mixed in dial space and compiled afresh")
        return _Node(compiled, g, "dna", notes, target)
    compiled = result
    lifted = lift(backend, compiled, reg)
    lifted = lifted.clone(lineage=["mix:morph " + " + ".join(
        f"{c.genome.name or '?'}x{w:g}" for c, w in children)])
    return _Node(compiled, lifted, "morph", notes, target)


def _try_morph(backend, children, register, target, notes):
    """Fold the parts left to right; None if the result is not an
    instrument (silent, off pitch) so the caller can fall back."""
    morph = MORPHS[backend.name]
    acc, weight = children[0][0].compiled.patch, children[0][1]
    for child, w in children[1:]:
        t = w / (weight + w)
        try:
            acc = morph(acc, child.compiled.patch, t)
        except Exception as error:               # a malformed patch is a fallback, not a crash
            notes.append(f"morph failed ({error}); fell back to dial space")
            return None
        weight += w
    base = children[0][0].compiled
    compiled = Compiled(backend.name, "mix", acc, dict(base.setup),
                        dict(base.style), list(base.notes))
    if backend.name == "nes":
        compiled.style["channel"] = acc.channel
        compiled.style["pitched"] = acc.channel != "noise"
    g = Genome(backend.name, target, name="mix", register=register)
    result = probe_mod.probe(backend, compiled, g, full=False)
    if not result.ok or not result.measurement.measured:
        notes.append("the morph did not make a usable instrument "
                     f"({', '.join(i.code for i in result.issues) or 'silent'})"
                     "; fell back to dial space")
        return None
    evolve.auto_level(backend, compiled, result)
    notes.append("morphed hardware parameters operator by operator"
                 if backend.name != "nes" else "morphed the macros")
    return compiled


def run_recipe(backend, recipe, bank, name: str, role: str = ""):
    """Evaluate a recipe into a bank Entry."""
    from .bank import Entry
    if isinstance(recipe, str):
        recipe = json.loads(recipe)
    register = None
    node = evaluate(backend, recipe, bank, register)
    compiled = node.compiled
    compiled.name = slug(name)
    g = node.genome.clone(name=compiled.name)
    result = probe_mod.probe(backend, compiled, g, full=True)
    measured = dict(result.axes)
    fit, _ = score_mod.fidelity(node.target, measured, backend.supports)
    return Entry(name=compiled.name, backend=backend.name, genome=g,
                 compiled=compiled, measured=measured,
                 score=fit * score_mod.quality(result.issues), role=role,
                 character="mix: " + "; ".join(node.notes) + ". "
                 + node.target.describe(0.22),
                 issues=[str(i) for i in result.issues])


def mix(backend, items, weights=None, mode: str = "auto", bank=None,
        name: str = "mix"):
    """Convenience: mix instruments by name. -> Entry."""
    if isinstance(backend, str):
        backend = get_backend(backend)
    weights = weights or [1.0] * len(items)
    recipe = {"mix": [{"from": i, "weight": w}
                      for i, w in zip(items, weights)], "mode": mode}
    return run_recipe(backend, recipe, bank, name)
