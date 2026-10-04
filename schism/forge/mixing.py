"""mixing.py — blend instruments into new ones, recursively, on purpose.

Three ways to mix, because there are three things worth preserving:

* **dna** — blend what the instruments *sound like*. The parents' measured
  dials are averaged (weighted), the family and structure of the heavier
  parent win, and the result is compiled afresh and corrected by the same
  loop as everything else. It works across families and for any two
  instruments, including one lifted out of a score's own `inst` line, and
  it never produces nonsense; what it does not do is keep any detail of
  either parent.
* **morph** — blend the *recipes*. Two tones are mixed partial by partial
  (a partial only one parent has fades with the weight), their envelopes
  point by point, their unison and vibrato and noise by amount; two modal
  instruments mode by mode; two drums of the same kind layer by layer. This
  keeps the specific, lumpy character of each, and it can fail, so the
  result is played and checked, and falls back to `dna` if it is silent or
  off pitch.

* **layer** — keep the *samples*. A stack takes the first moments of one
  sound, the body of another, the low end of a third and splices them in
  PCM (`fam_layer`): a kick's knock over a bass is a kick over a bass, not
  a duller bass. It is what `auto` does when the parts are not the same kind
  of sound, because a blend of dials keeps nothing of either parent, and
  what `--mode layer` or a `{"layer": [...]}` node asks for by name.

A mix of mixes is a tree, evaluated from the leaves, so "two parts bass,
one part bell, then half of that with brass" is one recipe. Every node is
measured, so the depth costs one render per node and nothing is guessed.

Recipes are JSON:

    {"mix": [{"from": "slap_bass", "weight": 2},
             {"mix": [{"from": "bell"}, {"from": "brass"}], "weight": 1}],
     "mode": "auto"}

    {"layer": [{"from": "clap", "role": "click", "ms": 30},
               {"from": "snare", "role": "body"}],
     "stereo": 6}

`from` names a bank instrument, a starter instrument, an archetype
(`archetype:NAME` or the bare name), or `recipe:wave=saw ...` (an `inst`
line's settings). A part of a layer takes its role (`click`, `body`,
`noise`, `sub`, `tail`; left out, it is decided from what the sounds are),
that role's options (`ms`, `from`, `fade`, `hp`, `lp`, `at`, `level`) and
`db`; the node takes `chord`, `root`, `stereo` and `rev`.
"""

import copy
import json
import math
from typing import Optional

from .. import model as M, recipes
from . import archetypes, evolve, fam_layer, probe as probe_mod, score as score_mod
from .dna import AXES, DNA, blend
from .family import Compiled, get_family
from .genome import Genome, slug
from .patch import Patch, PatchError

MAX_DEPTH = 6


class MixError(ValueError):
    pass


# --------------------------------------------------------------------------
# Lifting: from an instrument to a place in dial space
# --------------------------------------------------------------------------
def lift(compiled: Compiled, register=None, name: str = "") -> Genome:
    """Measure a compiled instrument and describe it as a genome whose DNA is
    what it measures."""
    family = get_family(compiled.family)
    reg = register or family.default_register
    g = Genome(family.name, DNA(), name=name or compiled.name, register=reg,
               style={"kind": compiled.style["kind"]}
               if compiled.style.get("kind") else {})
    result = probe_mod.probe(compiled, g, full=True)
    dna = DNA.clamped(**{a: (result.axes.get(a) or 0.0) for a in AXES})
    return g.clone(dna=dna, lineage=[f"lift:{compiled.name}"])


_ONE_SHOT_KIND = {"kick": "kick", "snare": "snare", "hat": "hat",
                  "openhat": "hat", "clap": "clap", "tom": "tom", "rim": "rim"}


def lift_recipe(text: str, family_hint: Optional[str] = None) -> Genome:
    """A genome that sounds like a score's `inst` line — `wave=saw oct=3-5
    fade=60`, or `wave=kick`. The line is compiled by the score compiler,
    played by the probe and measured; nothing is assumed about how it was
    made."""
    options = recipes.split_options(text.split())
    module = M.Module(title="lift", mix_volume=48)
    recipes.compile_instrument(1, dict(options), recipes.Library(), module)
    kind = options.get("wave")
    if "kit" in options:
        raise MixError("a kit is several sounds; lift one of its samples "
                       "with wave=kick (or snare, hat...)")
    if kind in _ONE_SHOT_KIND:
        family, style = "drum", {"kind": _ONE_SHOT_KIND[kind]}
        register = get_family("drum").kinds[style["kind"]][1]
    elif kind in ("pluck", "bell"):
        family, style, register = "modal", {"set": "string" if kind == "pluck"
                                            else "bell"}, ("A", 3)
    elif kind == "noise":
        family, style, register = "fx", {"kind": "swoosh"}, ("A", 3)
    else:
        family, style, register = "tone", {}, ("A", 3)
    if family_hint and get_family(family_hint).name != family:
        family = get_family(family_hint).name
    note = probe_mod.note_of(*register)
    pitched = family in ("tone", "modal")
    result = probe_mod.probe_instrument(module, 1, note, full=True,
                                        pitched=pitched)
    dna = DNA.clamped(**{a: (result.axes.get(a) or 0.0) for a in AXES})
    return Genome(family, dna, style=style, register=register, name="",
                  lineage=[f"lift:recipe {text}"])


def _recipe_node(spec: str, register):
    genome = lift_recipe(spec)
    g, compiled, _ = evolve.realise(genome.family, genome, probes=3, full=True)
    return _Node(compiled, g, "leaf", [f"recipe {spec}"])


# --------------------------------------------------------------------------
# Morphing: blend recipes
# --------------------------------------------------------------------------
def _lerp(a, b, t):
    return a * (1.0 - t) + b * t


def _geo(a, b, t):
    a, b = max(a, 1e-9), max(b, 1e-9)
    return math.exp(_lerp(math.log(a), math.log(b), t))


def _pick(a, b, t):
    return b if t >= 0.5 else a


def _blend_lists(la, lb, t, tol=0.045):
    """Mix two [(ratio, amplitude, *rest)] lists partial by partial: pair
    each partial with the nearest unpaired one of the other list within
    `tol` in ratio, and let an unpaired one fade with its parent's weight."""
    out = []
    used = set()
    for item in la:
        best, gap = None, tol
        for j, other in enumerate(lb):
            if j in used:
                continue
            d = abs(other[0] - item[0]) / max(item[0], 1e-9)
            if d < gap:
                best, gap = j, d
        if best is None:
            out.append([item[0], item[1] * (1.0 - t)] + list(item[2:]))
        else:
            used.add(best)
            other = lb[best]
            merged = [_geo(item[0], other[0], t),
                      _lerp(item[1], other[1], t)]
            for x, y in zip(item[2:], other[2:]):
                merged.append(_geo(x, y, t))
            out.append(merged)
    for j, other in enumerate(lb):
        if j not in used:
            out.append([other[0], other[1] * t] + list(other[2:]))
    out.sort(key=lambda p: p[0])
    return [p for p in out if p[1] > 1e-6]


def _blend_envelope(ea, eb, t, sa, sb):
    """Two [[seconds, level]] envelopes -> one, evaluated on the union of
    their times; the sustain pair follows the heavier parent's time."""
    def at(env, x):
        if x <= env[0][0]:
            return env[0][1]
        for (t0, v0), (t1, v1) in zip(env, env[1:]):
            if x <= t1:
                return v0 + (v1 - v0) * (x - t0) / max(t1 - t0, 1e-9)
        return env[-1][1]
    times = sorted({round(x, 5) for x, _ in ea} | {round(x, 5) for x, _ in eb})
    # no more than 25 nodes: keep the sustain times and thin the rest
    out = [[x, round(_lerp(at(ea, x), at(eb, x), t), 4)] for x in times]
    sus_time = (ea[sa[0]][0] if sa else None) if t < 0.5 else \
        (eb[sb[0]][0] if sb else None)
    while len(out) > 25:
        victim = min(range(1, len(out) - 1),
                     key=lambda i: abs(out[i][1] - 0.5 * (out[i - 1][1]
                                                          + out[i + 1][1]))
                     + (10.0 if sus_time is not None
                        and abs(out[i][0] - sus_time) < 1e-6 else 0.0))
        del out[victim]
    sustain = None
    if sus_time is not None:
        i = min(range(len(out)), key=lambda k: abs(out[k][0] - sus_time))
        sustain = [i, i]
    return out, sustain


def _morph_numbers(pa: dict, pb: dict, t: float, skip=()):
    out = {}
    for key in sorted(set(pa) | set(pb)):
        if key in skip:
            continue
        a, b = pa.get(key), pb.get(key)
        if isinstance(a, bool) or isinstance(b, bool):
            out[key] = _pick(a, b, t) if a is not None and b is not None \
                else (a if b is None else b)
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out[key] = _lerp(a, b, t)
        elif a is None:
            out[key] = b
        elif b is None:
            out[key] = a
        else:
            out[key] = _pick(a, b, t)
    return out


def morph_patch(a: Patch, b: Patch, t: float) -> Patch:
    """Blend two patches of one family. t=0 is `a`, t=1 is `b`."""
    if a.family != b.family:
        raise MixError(f"cannot morph a {a.family} patch with a {b.family} "
                       f"one; mix by dna")
    pa, pb = a.params, b.params
    if a.family == "tone":
        params = dict(pa if t < 0.5 else pb)
        params["partials"] = [[round(r, 5), round(x, 6)] for r, x, *_ in
                              _blend_lists(pa["partials"], pb["partials"], t)]
        if pa.get("long") or pb.get("long"):
            params["long"] = True
            params["frames"] = max(pa.get("frames", 32768),
                                   pb.get("frames", 32768))
            params["lfo_bins"] = 4 if params["frames"] == 32768 else 8
            va = {o: x for o, x in pa.get("voices", [[0, 1.0]])}
            vb = {o: x for o, x in pb.get("voices", [[0, 1.0]])}
            params["voices"] = [[o, round(_lerp(va.get(o, 0.0),
                                                vb.get(o, 0.0), t)
                                          if o else 1.0, 4)]
                                for o in sorted(set(va) | set(vb))
                                if (va.get(o, 0) or vb.get(o, 0))]
            for k in ("am", "pm", "noise"):
                params[k] = round(_lerp(pa.get(k, 0.0), pb.get(k, 0.0), t), 5)
        params["grid"] = max(pa.get("grid", 1), pb.get("grid", 1))
        params["attack_s"] = round(_lerp(pa.get("attack_s", 0.0),
                                         pb.get("attack_s", 0.0), t), 5)
        if "punch" in pa and "punch" in pb:
            params["punch"] = {
                "partials": [[round(r, 4), round(x, 6)] for r, x, *_ in
                             _blend_lists(pa["punch"]["partials"],
                                          pb["punch"]["partials"], t)],
                "hold": _lerp(pa["punch"]["hold"], pb["punch"]["hold"], t),
                "fall": _lerp(pa["punch"]["fall"], pb["punch"]["fall"], t)}
        if ("bake" in pa) != ("bake" in pb):
            # one rings in the sample and the other in the envelope; the
            # heavier one's way wins
            heavy = pa if t < 0.5 else pb
            params.pop("bake", None)
            if "bake" in heavy:
                params["bake"] = heavy["bake"]
        elif "bake" in pa:
            params["bake"] = {"tau": round(_geo(pa["bake"]["tau"],
                                                pb["bake"]["tau"], t), 5),
                              "level": round(_lerp(pa["bake"]["level"],
                                                   pb["bake"]["level"], t), 4)}
    elif a.family == "modal":
        params = dict(pa if t < 0.5 else pb)
        params["modes"] = [[round(r, 5), round(x, 6), round(tau, 5)]
                           for r, x, tau in _blend_lists(pa["modes"],
                                                         pb["modes"], t)]
        params["length"] = round(_lerp(pa["length"], pb["length"], t), 3)
        params["attack_s"] = round(_lerp(pa.get("attack_s", 0.0),
                                         pb.get("attack_s", 0.0), t), 5)
        for key in ("pair", "tremolo", "noise"):
            xa, xb = pa.get(key), pb.get(key)
            if xa and xb:
                params[key] = {k: round(_lerp(xa[k], xb[k], t), 4)
                               for k in xa}
            elif xa or xb:
                heavy = xa if t < 0.5 else xb
                params.pop(key, None)
                if heavy:
                    params[key] = heavy
    else:                                    # layered: drum, fx
        if pa.get("layers") is None or pb.get("layers") is None:
            raise MixError("a layered patch without layers")
        params = dict(pa if t < 0.5 else pb)
        merged = []
        for x, y in zip(pa["layers"], pb["layers"]):
            if x["t"] != y["t"]:
                merged.append(copy.deepcopy(x if t < 0.5 else y))
                continue
            layer = {}
            for key in sorted(set(x) | set(y)):
                vx, vy = x.get(key), y.get(key)
                if isinstance(vx, (int, float)) and isinstance(vy, (int, float)) \
                        and not isinstance(vx, bool):
                    layer[key] = round(_geo(vx, vy, t) if vx > 0 and vy > 0
                                       and key not in ("amp", "at")
                                       else _lerp(vx, vy, t), 5)
                else:
                    layer[key] = vx if t < 0.5 and vx is not None else (
                        vy if vy is not None else vx)
            merged.append(layer)
        longer = pa["layers"] if len(pa["layers"]) > len(pb["layers"]) \
            else pb["layers"]
        weight = (1.0 - t) if longer is pa["layers"] else t
        for extra in longer[len(merged):]:
            item = copy.deepcopy(extra)
            item["amp"] = round(item.get("amp", 1.0) * weight, 5)
            merged.append(item)
        params["layers"] = merged
        params["length"] = round(_lerp(pa["length"], pb["length"], t), 3)
        params["drive"] = round(_lerp(pa.get("drive", 0.0),
                                      pb.get("drive", 0.0), t), 4)
        params["reg_note"] = pa["reg_note"] if t < 0.5 else pb["reg_note"]
    out = Patch(a.family, params)
    if a.amp and b.amp and a.amp_sustain is not None or b.amp_sustain is not None:
        out.amp, out.amp_sustain = _blend_envelope(
            a.amp, b.amp, t, a.amp_sustain, b.amp_sustain)
    else:
        out.amp, out.amp_sustain = _blend_envelope(a.amp, b.amp, t, None, None)
    heavy = a if t < 0.5 else b
    out.inst = dict(heavy.inst)
    out.inst["gain"] = int(round(_lerp(a.inst.get("gain", 128),
                                       b.inst.get("gain", 128), t) / 2.0)) * 2
    out.vib, out.cutoff, out.resonance = heavy.vib, heavy.cutoff, heavy.resonance
    out.filter_env, out.filter_sustain = heavy.filter_env, heavy.filter_sustain
    out.pan_env, out.pitch_env = heavy.pan_env, heavy.pitch_env
    out.bands = heavy.bands
    return out


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


def _leaf(spec, bank, register):
    if spec.startswith("archetype:"):
        arch = archetypes.get(spec.split(":", 1)[1])
        g = Genome(arch.family, arch.dna, style=dict(arch.style),
                   name=slug(spec), register=arch.register)
        g, compiled, _ = evolve.realise(arch.family, g, probes=3, full=True)
        return _Node(compiled, g, "leaf", [f"archetype {spec}"])
    if spec.startswith("recipe:"):
        return _recipe_node(spec[7:], register)
    from . import notation_hook
    for source, label in ((bank, "bank"), (notation_hook.starter(), "starter")):
        if source is None:
            continue
        for entry in source.entries:
            if entry.name == spec:
                family = get_family(entry.family)
                compiled = family.from_dict(family.to_dict(entry.compiled))
                compiled.name = entry.name
                return _Node(compiled, entry.genome, "leaf",
                             [f"{label} {spec}"])
    if spec in archetypes.names():
        return _leaf("archetype:" + spec, bank, register)
    raise MixError(f"no instrument {spec!r}: it is not in the bank being "
                   f"built or the starter bank, not an archetype and not "
                   f"recipe:wave=...")


#: the keys of a layer part that are not options of its role
_PART_KEYS = ("from", "mix", "layer", "role", "weight", "mode", "name")


def _label(part: dict) -> str:
    if part.get("name"):
        return str(part["name"])
    spec = part.get("from")
    if not isinstance(spec, str):
        return "mix"
    if spec.startswith("archetype:"):
        return spec.split(":", 1)[1]
    return "recipe" if spec.startswith("recipe:") else spec


def edits_of(node: dict) -> dict:
    """The `chord`, `root`, `stereo` and `rev` of a layer node, in the form
    a patch is edited with."""
    from . import splice
    edits = {}
    if node.get("chord"):
        try:
            splice.parse_chord(node["chord"])
        except splice.SpliceError as error:
            raise MixError(str(error)) from None
        edits["chord"] = {"notes": node["chord"],
                          "root": int(node.get("root", 0))}
    elif node.get("root"):
        raise MixError("root moves a chord: it needs chord")
    if node.get("stereo"):
        edits["stereo"] = float(node["stereo"])
    if node.get("rev"):
        edits["rev"] = True
    return edits


def _stack(sources, edits, register, notes) -> "_Node":
    """A stack of the sources, played, levelled and described by what it
    measures."""
    try:
        genome, compiled = fam_layer.build_stack(sources, "layer", edits,
                                                 register)
    except PatchError as error:
        raise MixError(str(error)) from None
    result = probe_mod.probe(compiled, genome, full=True)
    evolve.auto_level(get_family("layer"), compiled, result)
    dna = DNA.clamped(**{a: (result.axes.get(a) or 0.0) for a in AXES})
    notes.extend(compiled.notes)
    return _Node(compiled, genome.clone(dna=dna), "layer", notes, dna)


def _layer_node(node: dict, bank, register, depth: int) -> "_Node":
    parts = node["layer"]
    if not isinstance(parts, list) or len(parts) < 2:
        raise MixError("a layer needs at least two parts")
    sources = []
    for part in parts:
        child = evaluate(part, bank, register, depth + 1, None)
        options = {k: v for k, v in part.items() if k not in _PART_KEYS}
        try:
            sources.append(fam_layer.Source(
                _label(part), child.compiled, child.genome, part.get("role"),
                options, float(part.get("weight", 1.0))))
        except PatchError as error:
            raise MixError(str(error)) from None
    return _stack(sources, edits_of(node), register, [])


def _alike(children) -> bool:
    """Are these the same kind of sound, so that blending them means
    something? Two tones are; a tone and a drum are not; a kick and a snare
    are two drums that are not alike."""
    families = {c.genome.family for c, *_ in children}
    if len(families) != 1:
        return False
    if families <= {"drum", "fx"}:
        return len({c.compiled.style.get("kind") for c, *_ in children}) == 1
    return True


def evaluate(node: dict, bank=None, register=None, depth: int = 0,
             family: Optional[str] = None) -> _Node:
    if depth > MAX_DEPTH:
        raise MixError(f"a mix nests more than {MAX_DEPTH} deep")
    if "from" in node:
        return _leaf(str(node["from"]), bank, register)
    if "layer" in node:
        return _layer_node(node, bank, register, depth)
    parts = node.get("mix")
    if not parts or len(parts) < 2:
        raise MixError("a mix needs at least two parts")
    children = []
    for part in parts:
        child = evaluate(part, bank, register, depth + 1, family)
        children.append((child, float(part.get("weight", 1.0)), part))
    if any(w <= 0 for _, w, _ in children):
        raise MixError("mix weights must be positive")
    mode = node.get("mode", "auto")
    if mode == "layer" or (mode == "auto" and family is None
                           and not _alike(children)):
        # different sounds keep their samples; averaging their dials keeps
        # neither (a kick over a bass would come out a duller bass)
        sources = [fam_layer.Source(_label(part), c.compiled, c.genome,
                                    part.get("role"),
                                    {k: v for k, v in part.items()
                                     if k not in _PART_KEYS}, w)
                   for c, w, part in children]
        try:
            return _stack(sources, edits_of(node), register, [])
        except MixError as error:
            if mode == "layer":
                raise
            note = f"could not stack them ({error}); mixed by dial"
    else:
        note = ""
    children = [(c, w) for c, w, _ in children]
    target = blend([(c.target, w) for c, w in children])
    heavy = max(children, key=lambda cw: cw[1])[0]
    fam = get_family(family or heavy.genome.family)
    reg = register or heavy.genome.register
    notes = [note] if note else []
    if mode in ("morph", "auto"):
        done = _try_morph(children, reg, target, notes)
        if done is not None:
            compiled, lifted = done
            lifted = lifted.clone(lineage=["mix:morph " + " + ".join(
                f"{c.genome.name or '?'}x{w:g}" for c, w in children)])
            return _Node(compiled, lifted, "morph", notes, target)
    style = dict(heavy.genome.style)
    g = Genome(fam.name, target, style=style, name="", register=reg,
               lineage=["mix:dna " + " + ".join(
                   f"{c.genome.name or '?'}x{w:g}" for c, w in children)])
    g, compiled, _ = evolve.realise(fam, g, probes=3, full=True)
    notes.append("mixed in dial space and compiled afresh")
    return _Node(compiled, g, "dna", notes, target)


def _try_morph(children, register, target, notes):
    """Fold the parts left to right; None if the result is not an
    instrument (silent, off pitch) so the caller can fall back."""
    families = {c.genome.family for c, _ in children}
    if len(families) != 1:
        notes.append("the parts are different families; mixed by dial")
        return None
    acc, weight = children[0][0].compiled.patch, children[0][1]
    for child, w in children[1:]:
        t = w / (weight + w)
        try:
            acc = morph_patch(acc, child.compiled.patch, t)
        except (MixError, KeyError, TypeError, ValueError) as error:
            notes.append(f"morph failed ({error}); fell back to dial space")
            return None
        weight += w
    base = children[0][0].compiled
    compiled = Compiled(base.family, "mix", acc, dict(base.setup),
                        dict(base.style), list(base.notes))
    if acc.problems():
        notes.append("the morph was not a legal instrument ("
                     + acc.problems()[0] + "); fell back to dial space")
        return None
    g = Genome(base.family, target, name="mix", register=register,
               style=dict(children[0][0].genome.style))
    try:
        result = probe_mod.probe(compiled, g, full=False)
    except Exception as error:               # a morph that does not render
        notes.append(f"the morph did not render ({error}); fell back to "
                     f"dial space")
        return None
    if not result.ok or not result.measurement.measured:
        notes.append("the morph did not make a usable instrument "
                     f"({', '.join(i.code for i in result.issues) or 'silent'})"
                     "; fell back to dial space")
        return None
    evolve.auto_level(get_family(base.family), compiled, result)
    notes.append("morphed the recipes part by part")
    return compiled, lift(compiled, register)


def run_recipe(recipe, bank, name: str, role: str = "",
               family: Optional[str] = None):
    """Evaluate a recipe into a bank Entry."""
    from .bank import Entry
    if isinstance(recipe, str):
        recipe = json.loads(recipe)
    node = evaluate(recipe, bank, None, 0, family)
    compiled = node.compiled
    compiled.name = slug(name)
    g = node.genome.clone(name=compiled.name)
    fam = get_family(compiled.family)
    result = probe_mod.probe(compiled, g, full=True)
    measured = dict(result.axes)
    fit, _ = score_mod.fidelity(node.target, measured, fam.supports)
    return Entry(name=compiled.name, family=compiled.family, genome=g,
                 compiled=compiled, measured=measured,
                 score=fit * score_mod.quality(result.issues), role=role,
                 character=("layer" if node.mode == "layer" else "mix")
                 + ": " + "; ".join(node.notes) + ". "
                 + node.target.describe(0.22),
                 issues=[str(i) for i in result.issues])


def mix(items, weights=None, mode: str = "auto", bank=None,
        name: str = "mix", family: Optional[str] = None):
    """Convenience: mix instruments by name. -> Entry."""
    weights = weights or [1.0] * len(items)
    recipe = {"mix": [{"from": i, "weight": w}
                      for i, w in zip(items, weights)], "mode": mode}
    return run_recipe(recipe, bank, name, family=family)


def layer(parts, bank=None, name: str = "layer", edits: Optional[dict] = None):
    """Convenience: a stack of `parts`, each a dict like
    {"from": "clap", "role": "click", "ms": 30}. -> Entry."""
    recipe = {"layer": list(parts)}
    recipe.update(edits or {})
    return run_recipe(recipe, bank, name)
