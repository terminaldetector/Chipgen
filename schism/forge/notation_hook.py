"""notation_hook.py — how the score language reaches the forge.

A score can say

    bank mybank.json
    inst 1 name=Bell  forge=bell dna=brightness:0.8,decay:0.3 reg=A4
    inst 2 name=Bass  forge=starter:punch_bass nna=cut
    smp   thump forge=kick dna=punch:0.9
    inst 3 name=Kit   kit=C-2:thump,D-2:@snare,F#2:@hat
    inst 4 name=Stab  forge=epiano chord=min9 root=-3 stereo=6
    smp   crash forge=cymbal rev=on
    inst 5 name=Crack forge=layer:clap@click+snare@body

and the forge makes the sound: from a bank (instant: the patch is in the
file), or from an archetype (the dials are realised against the player
first, which costs a few tenths of a second, once per distinct sound in a
process). `dna=` moves dials; `reg=` is the register (for a drum, the pitch
it is tuned to). Everything else on the line is the ordinary instrument
settings, applied on top.

The envelopes of a patch are in seconds. A score's tick is 2.5/tempo
seconds, and the tempo is only final once the whole header has been read, so
`retime` times every forged instrument again at the end.
"""

import copy
import os
from typing import Dict, Optional

from .. import model as M, recipes
from . import archetypes, evolve, fam_layer, patch as patch_mod
from .probe import ProbeUnavailable
from .bank import Bank, BankError
from .dna import AXES, _canonical
from .family import get_family
from .genome import Genome
from .patch import REFERENCE_TICK

_REALISED: Dict[str, tuple] = {}
_STARTER: Optional[Bank] = None


def starter_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "banks",
                        "starter.bank.json")


def starter() -> Optional[Bank]:
    """The bank that ships with the forge, or None if it is not there."""
    global _STARTER
    if _STARTER is None:
        path = starter_path()
        if os.path.exists(path):
            _STARTER = Bank.load(path)
        else:
            _STARTER = Bank("starter")
    return _STARTER


def load_bank(library, path: str):
    """`bank PATH`: relative paths start where the score is."""
    if not path:
        raise recipes.RecipeError("`bank` needs a path", "bank my.bank.json")
    full = path if os.path.isabs(path) else os.path.join(library.base_dir, path)
    try:
        library.banks.append(Bank.load(full))
    except BankError as error:
        raise recipes.RecipeError(str(error), "make one with `python3 -m "
                                  "schism forge run SCENARIO.json`") from None


def parse_dna(text: str):
    """'brightness:0.8,decay:0.3' -> {axis: value}."""
    out = {}
    for part in filter(None, (text or "").split(",")):
        if ":" not in part:
            raise recipes.RecipeError(
                f"{part!r} is not axis:value",
                "write dna=brightness:0.8,decay:0.3")
        key, value = part.split(":", 1)
        axis = _canonical(key)
        if axis is None:
            raise recipes.RecipeError(
                f"{key!r} is not a DNA axis", "the axes are "
                + ", ".join(AXES))
        try:
            number = float(value)
        except ValueError:
            raise recipes.RecipeError(
                f"{axis}: {value!r} is not a number") from None
        if not 0.0 <= number <= 1.0:
            raise recipes.RecipeError(f"{axis}: {number:g} is outside 0..1")
        out[axis] = number
    return out


def parse_register(text: str):
    t = (text or "").strip().replace("-", "")
    if len(t) < 2 or t[0].upper() not in "ABCDEFG" or not t[-1].isdigit():
        raise recipes.RecipeError(f"reg={text!r} is not a note like A3 or "
                                  f"C#4")
    name = t[0].upper() + ("#" if "#" in t[1:-1] else "")
    return name, int(t[-1])


def resolve(spec: str, library, dna_text: str = "", reg_text: str = ""):
    """-> (Genome, Compiled): a sound by name. Names are looked up in the
    banks the score loaded, then in the starter bank, then among the
    archetypes."""
    if spec.startswith("layer:"):
        if dna_text or reg_text:
            raise recipes.RecipeError(
                "dna= and reg= belong to one sound; a layer is several",
                "move the dial on a part, or forge the part first with "
                "`python3 -m schism forge compile`")
        return resolve_layer(spec[6:], library)
    overrides = parse_dna(dna_text)
    register = parse_register(reg_text) if reg_text else None
    name = spec
    wanted = None
    if ":" in spec:
        wanted, name = spec.split(":", 1)
    entry = None
    if wanted in (None, "bank"):
        for bank in list(library.banks) + [starter()]:
            if bank is not None and name in bank.names():
                entry = bank.get(name)
                break
    elif wanted == "starter":
        bank = starter()
        if bank is not None and name in bank.names():
            entry = bank.get(name)
    elif wanted not in ("archetype",):
        raise recipes.RecipeError(
            f"forge={spec!r}: {wanted!r} is not bank:, starter: or "
            f"archetype:", "write forge=NAME, or forge=bank:NAME")
    if entry is not None and not overrides and register is None:
        family = get_family(entry.family)
        compiled = family.from_dict(family.to_dict(entry.compiled))
        return entry.genome, compiled
    if entry is not None:
        genome = entry.genome
    else:
        try:
            arch = archetypes.get(name)
        except KeyError:
            known = sorted(set(archetypes.names())
                           | {n for b in library.banks for n in b.names()}
                           | (set(starter().names()) if starter() else set()))
            raise recipes.RecipeError(
                f"forge={spec!r}: no archetype or bank instrument of that "
                f"name", "have: " + ", ".join(known)) from None
        genome = Genome(arch.family, arch.dna, style=dict(arch.style),
                        register=arch.register, name=name,
                        lineage=[f"archetype:{name}"])
    if overrides:
        genome = genome.clone(dna=genome.dna.with_(**overrides), bias={})
    if register is not None:
        genome = genome.clone(register=register)
    key = genome.key() + ":" + genome.family
    if key not in _REALISED:
        try:
            g, compiled, _ = evolve.realise(genome.family, genome, probes=3,
                                            full=True)
        except ProbeUnavailable:
            raise recipes.RecipeError(
                f"forge={spec}: a sound the forge makes fresh (an archetype, "
                f"or a bank instrument with dna= or reg=) is rendered and "
                f"measured, and libopenmpt is not installed",
                "apt install libopenmpt0; or write forge=NAME with no dna= "
                "and no reg= to take it ready-made from the starter bank "
                "(`python3 -m schism forge archetypes` lists the names)") from None
        _REALISED[key] = (g, compiled)
    g, compiled = _REALISED[key]
    family = get_family(compiled.family)
    return g, family.from_dict(family.to_dict(compiled))


_STACKS: Dict[str, tuple] = {}


def parse_layer(text: str):
    """'clap@click/ms=40+snare@body' -> [(name, role or None, options)]."""
    out = []
    for token in text.split("+"):
        if not token.strip():
            raise recipes.RecipeError(
                "an empty part in forge=layer:", "write forge=layer:"
                "clap@click+snare@body; the roles are "
                + ", ".join(fam_layer.ROLES))
        try:
            out.append(fam_layer.parse_part(token))
        except fam_layer.PatchError as error:
            raise recipes.RecipeError(str(error)) from None
    return out


def resolve_layer(text: str, library, edits: Optional[dict] = None):
    """-> (Genome, Compiled): a stack of sounds by name, each found as
    `resolve` finds it."""
    parts = parse_layer(text)
    if len(parts) < 2:
        raise recipes.RecipeError(
            f"forge=layer:{text} has one part; a layer stacks at least two",
            "write forge=layer:clap@click+snare@body")
    key = text + "|" + repr(sorted((edits or {}).items(), key=str)) \
        + "|" + ",".join(b.name for b in library.banks)
    if key not in _STACKS:
        sources = []
        for name, role, options in parts:
            genome, compiled = resolve(name, library)
            sources.append(fam_layer.Source(name.split(":")[-1], compiled,
                                            genome, role, options))
        try:
            _STACKS[key] = fam_layer.build_stack(
                sources, "layer", edits or {})
        except (fam_layer.PatchError, ValueError) as error:
            raise recipes.RecipeError(f"forge=layer:{text}: {error}") from None
    genome, compiled = _STACKS[key]
    return genome, copy.deepcopy(compiled)


def take_edits(options: dict) -> dict:
    """Remove `chord`, `root`, `stereo` and `rev` from a score's options and
    return them as the edits a patch is given."""
    found = {}
    for key in ("chord", "root", "stereo", "rev"):
        if key not in options:
            continue
        text = options.pop(key)
        try:
            found[key] = recipes.SETTINGS[key][0](text)
        except recipes.RecipeError as error:
            raise recipes.RecipeError(f"{key}: {error}") from None
    edits = {}
    root = found.get("root", 0)
    if "chord" in found:
        edits["chord"] = {"notes": found["chord"], "root": root}
    elif root:
        raise recipes.RecipeError("root= moves a chord and there is no "
                                  "chord=", "write chord=min9 root=-3")
    if found.get("stereo"):
        edits["stereo"] = found["stereo"]
    if found.get("rev"):
        edits["rev"] = True
    return edits


# -- an instrument line ---------------------------------------------------------------
_ENVELOPES = (("venv", "volume"), ("penv", "pan"), ("ienv", "pitch"),
              ("fenv", "filter"))


def find_kit(library, name: str):
    """-> (Bank, kit dict) for a kit of the banks the score loaded or of the
    starter bank."""
    for bank in list(library.banks) + [starter()]:
        if bank is None:
            continue
        for kit in bank.kits:
            if kit["name"] == name:
                return bank, kit
    have = sorted({k["name"] for b in list(library.banks) + [starter()]
                   if b is not None for k in b.kits})
    raise recipes.RecipeError(
        f"no kit named {name!r}", "have: " + (", ".join(have) or
                                              "none; make one with `python3 "
                                              "-m schism forge kit`"))


def _compile_kit(number: int, name: str, options: dict, library,
                 module: M.Module, warn):
    """`inst N forge=kit:NAME`: one instrument, a drum on each key."""
    bank, kit = find_kit(library, name)
    label = options.pop("name", None) or name
    ins = M.Instrument(name=label[:25])
    ins.nna = 0
    volume_scale = options.pop("vol", None)
    gain = options.pop("gain", None)
    if gain is not None:
        ins.global_volume = recipes._int(0, 128)(gain)
    pan = options.pop("pan", None)
    if pan is not None:
        ins.default_pan = recipes._int(0, 64)(pan)
    if options:
        names = sorted(options)
        raise recipes.RecipeError(
            f"{', '.join(names)} not settings of a kit",
            "a kit takes: name, gain, pan, vol")
    for key_name, member in kit["keys"]:
        key = recipes._note(key_name)
        entry = bank.get(member)
        patch = entry.compiled.patch
        built = patch_mod.make_sample(patch.family, patch.params, 61, 61)
        speed = 44100 if patch.params.get("reg_note") is not None \
            else built.c5speed
        level = int(round(patch.inst.get("gain", 128) / 2.0))
        if volume_scale is not None:
            level = int(round(level * recipes._int(0, 64)(volume_scale) / 64.0))
        module.samples.append(M.Sample(
            name=member[:25], data=built.data, right=built.right,
            c5speed=speed, loop=built.loop, volume=max(1, min(64, level))))
        ins.note_map[key - 1] = (60, len(module.samples))
    while len(module.instruments) < number:
        module.instruments.append(M.Instrument(
            name="", note_map=[(n, 0) for n in range(120)]))
    module.instruments[number - 1] = ins


def compile_forged(number: int, options: dict, library, module: M.Module,
                   warn=None):
    options = dict(options)
    spec = options.pop("forge")
    if spec.startswith("kit:"):
        return _compile_kit(number, spec[4:], options, library, module, warn)
    edits = take_edits(options)
    if spec.startswith("layer:"):
        if options.get("dna") or options.get("reg"):
            raise recipes.RecipeError(
                "dna= and reg= belong to one sound; a layer is several",
                "move the dial on a part")
        genome, compiled = resolve_layer(spec[6:], library, edits)
        patch = compiled.patch.clone()
    else:
        genome, compiled = resolve(spec, library, options.pop("dna", ""),
                                   options.pop("reg", ""))
        patch = compiled.patch.clone()
        if edits:
            try:
                patch, short = patch_mod.with_edits(patch, edits)
            except (ValueError, KeyError) as error:
                raise recipes.RecipeError(f"forge={spec}: {error}") from None
            compiled.patch = patch.clone()       # it is what it was made into
            if warn and short > 0.5:
                warn(f"instrument {number:02d}: the edit leaves it "
                     f"{short:.1f} dB quieter than the plain sound, even at "
                     f"full instrument gain")
    tick = library.tick or REFERENCE_TICK

    def take(key):
        if key not in options:
            return None
        text = options.pop(key)
        parser = recipes.SETTINGS[key][0]
        try:
            return parser(text) if parser else text
        except recipes.RecipeError as error:
            raise recipes.RecipeError(f"{key}: {error}") from None

    octaves = take("oct")
    if octaves:
        patch.bands = (octaves[0] * 12 + 1, octaves[1] * 12 + 12,
                       patch.bands[2])
    name = take("name")
    for key in ("nna", "dct", "dca"):
        value = take(key)
        if value is not None:
            patch.inst[key] = value
    for key, field in (("fade", "fade"), ("pps", "pps"), ("gain", "gain"),
                       ("pan", "pan"), ("rv", "rv"), ("rp", "rp"),
                       ("vol", "vol")):
        value = take(key)
        if value is not None:
            patch.inst[field] = value
    ppc = take("ppc")
    if ppc is not None:
        patch.inst["ppc"] = ppc - 1
    for key, field in (("cutoff", "cutoff"), ("res", "resonance")):
        value = take(key)
        if value is not None:
            setattr(patch, field, value)
    vibrato = options.pop("vib", None)
    if vibrato:
        parsed = recipes._parse_vibrato(vibrato)
        patch.vib = list(parsed)
        if warn and parsed[1] and not parsed[2]:
            warn(f"instrument {number:02d} has vib={vibrato}: a rate of 0 "
                 f"never fades the vibrato in, so nothing wobbles")
    overridden = {}
    for key, kind in _ENVELOPES:
        if key in options:
            overridden[kind] = recipes.parse_envelope(options.pop(key), key)
    if "pitch" in overridden and "filter" in overridden:
        raise recipes.RecipeError(
            "ienv and fenv are the same envelope slot — the file has one "
            "pitch/filter envelope and a flag that says which",
            "give an instrument one of them")
    if options:
        names = sorted(options)
        many = len(names) > 1
        raise recipes.RecipeError(
            f"{', '.join(names)} {'are' if many else 'is'} not "
            f"{'settings' if many else 'a setting'} of a forged instrument",
            "forge= takes: dna, reg, chord, root, stereo, rev, name, nna, "
            "dct, dca, fade, pps, ppc, gain, pan, rv, rp, vol, cutoff, res, "
            "vib, oct, venv, penv, ienv, fenv")
    if warn and patch.inst.get("gain", 128) < 2:
        warn(f"instrument {number:02d} has gain={patch.inst['gain']}: the "
             f"player applies instrument volume in steps of 2, so it plays "
             f"silent. Use gain=2 or more (quiet is 8, full is 128)")
    problems = patch.problems()
    if problems:
        raise recipes.RecipeError(f"forge={spec}: " + "; ".join(problems[:2]))
    label = name if name is not None else (
        "+".join(part[0].split(":")[-1] for part in parse_layer(spec[6:]))
        if spec.startswith("layer:")
        else spec.split(":")[-1].replace("_", " "))
    before = len(module.samples)
    patch_mod.install(patch, module, number, tick, label)
    ins = module.instruments[number - 1]
    for kind, env in overridden.items():
        if kind == "volume":
            ins.volume_envelope = env
        elif kind == "pan":
            ins.pan_envelope = env
        else:
            ins.pitch_envelope = env
            if env.filter and ins.cutoff is None:
                ins.cutoff = 127
    if len(module.samples) == before:
        raise recipes.RecipeError(
            f"forge={spec} makes no sample in the notes it covers "
            f"({M.note_name(patch.bands[0])}..{M.note_name(patch.bands[1])})",
            "widen oct=")
    library.forged[number] = {"genome": genome, "compiled": compiled,
                              "patch": patch, "overridden": set(overridden),
                              "label": label}


def forged_samples(params: dict, library=None):
    """The samples a `smp NAME forge=...` (or a kit's @name) stands for:
    [(low, high, Sample)], one sample for every key, like the plain
    one-shots. Drums and effects play at their recorded pitch at C-5, which
    is where a kit key plays them; a modal sound is tuned to C-5."""
    holder = library if library is not None else _EmptyLibrary()
    genome, compiled = resolve(params["spec"], holder, params.get("dna", ""),
                               params.get("reg", ""))
    patch = compiled.patch
    if patch.family == "tone" or patch.params.get("looped"):
        raise recipes.RecipeError(
            f"forge={params['spec']} is a loop and needs one sample per "
            f"octave",
            "use it as inst N forge=...; smp and kit take drums, effects "
            "and plucked or struck sounds")
    if params.get("edits"):
        import json
        patch, _ = patch_mod.with_edits(patch, json.loads(params["edits"]))
    built = patch_mod.make_sample(patch.family, patch.params, 61, 61)
    c5speed = built.c5speed
    if patch.params.get("reg_note") is not None:
        c5speed = 44100          # recorded at 44.1 kHz; C-5 plays it as is
    sample = M.Sample(data=built.data, right=built.right, c5speed=c5speed,
                      loop=built.loop, volume=int(patch.inst.get("vol", 64)))
    return [(M.MIN_NOTE, M.MAX_NOTE, sample)]


class _EmptyLibrary:
    banks = []


def retime(module: M.Module, library, tick: float):
    """Time every forged instrument's envelopes against the score's real
    tick. An envelope the score wrote by hand stays as written."""
    for number, info in library.forged.items():
        ins = module.instruments[number - 1]
        volume, pan, pitch = patch_mod.build_envelopes(info["patch"], tick)
        if "volume" not in info["overridden"]:
            ins.volume_envelope = volume
        if "pan" not in info["overridden"]:
            ins.pan_envelope = pan
        if not ({"pitch", "filter"} & info["overridden"]):
            ins.pitch_envelope = pitch
            if pitch is not None and pitch.filter and ins.cutoff is None:
                ins.cutoff = 127


def forged_fm(library) -> dict:
    """{instrument number: the operator dump} of every instrument whose
    sound is an FM voice, so that a console export is not only PCM."""
    out = {}
    for number, info in library.forged.items():
        params = info["patch"].params
        if info["patch"].family == "fm" and params.get("fm"):
            out[number] = copy.deepcopy(params["fm"])
    return out


def forged_dac(library) -> dict:
    """{instrument number: how the DAC plays it} for the drums that a
    console plays through its one DAC channel: a drum with a `dac` in its
    recipe, or a stack with such a drum in it."""
    out = {}
    for number, info in library.forged.items():
        params = info["patch"].params
        found = params.get("dac")
        for part in params.get("parts", []):
            found = found or part["patch"].get("params", {}).get("dac")
        if found:
            out[number] = dict(found)
    return out


def forged_of(library) -> dict:
    """{instrument number: (Genome, Compiled)} for the arrangement model."""
    return {n: (i["genome"], i["compiled"]) for n, i in library.forged.items()}
