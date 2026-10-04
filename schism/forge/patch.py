"""patch.py — the instrument a genome compiles to, before it is a module.

A `Patch` is everything an Impulse Tracker instrument is, in a form that is
(a) small enough to keep in a JSON bank and (b) independent of the module it
will be played in:

  * what the **samples** are made of (`family` + `params`: a recipe, never
    PCM — the same recipe makes the same bytes, so a bank of fifty
    instruments is a few hundred kilobytes of text);
  * the **envelopes** in *seconds*, not ticks. A tick is 2.5/tempo seconds
    and the tempo belongs to the song; the patch is turned into ticks when
    it is installed in a module, at that module's tempo;
  * the instrument's own switches: NNA, duplicate check, fadeout, gain, pan,
    the filter, sample auto-vibrato.

`install` is the one place a patch becomes bytes-in-waiting: it builds the
samples (cached, since the same recipe is asked for again and again by a
search), converts the envelopes, and writes the instrument and its samples
into a `model.Module`.
"""

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .. import model as M

#: the tick a patch is judged at: tempo 125, the IT default and the module
#: tempo that `speed 6` songs at ordinary BPM end up near
REFERENCE_TICK = 2.5 / 125.0

MAX_NODES = 25


class PatchError(ValueError):
    """A patch the file format cannot hold, with the field named."""


# -- the sample makers ----------------------------------------------------------
#: family -> fn(params, low, high) -> spectral.Built: the one sample that
#: serves notes `low`..`high`
SAMPLERS: Dict[str, Any] = {}
#: family -> the edits of EDITS its sampler makes itself (a tone builds a
#: chord or a stereo pair in the spectrum, where a loop can stay a loop)
HANDLES: Dict[str, tuple] = {}
#: edits any recipe may ask for, by a key of its params, and made on the
#: finished sample unless the family makes them: `chord` (semitones above
#: the key), `stereo` (cents either side), `rev` (backwards)
EDITS = ("chord", "stereo", "rev")
_CACHE: Dict[str, Any] = {}
_CACHE_LIMIT = 256


def register_sampler(family: str, fn, handles: tuple = ()):
    SAMPLERS[family] = fn
    HANDLES[family] = tuple(handles)
    return fn


def make_sample(family: str, params: dict, low: int, high: int):
    """-> Built, for the notes `low`..`high`. Cached on the recipe: a search
    asks for the same recipe again and again."""
    key = json.dumps([family, params, low, high], sort_keys=True, default=str)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    try:
        sampler = SAMPLERS[family]
    except KeyError:
        raise PatchError(f"no sample family {family!r}; have: "
                         f"{', '.join(sorted(SAMPLERS))}") from None
    asked = [k for k in EDITS if params.get(k)]
    if asked:
        made = _edited(family, sampler, params, asked, low, high)
    else:
        made = sampler(params, low, high)
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = made
    return made


def is_pitched(family: str, params: dict) -> bool:
    """Does a sound of this family and recipe follow the key? A drum does
    not; a stack follows whatever holds its loop."""
    if family == "layer":
        return bool(params.get("pitched", True))
    from .family import get_family
    return bool(get_family(family).pitched)


def _edited(family, sampler, params, asked, low, high):
    """The sample with the edits a recipe asks for. What the family does not
    make itself is done on its finished sample."""
    from . import splice
    own = HANDLES.get(family, ())
    rest = {k: params[k] for k in asked if k not in own}
    plain = {k: v for k, v in params.items() if k not in EDITS}
    made = sampler({**plain, **{k: params[k] for k in asked if k in own}},
                   low, high)
    if rest:
        made = splice.finish(made, rest, low, high,
                             is_pitched(family, params))
    return made


def with_edits(patch: "Patch", edits: dict):
    """-> (Patch, dB). The patch with a chord, a stereo pair or a reverse
    written into its recipe, and its instrument gain raised by as much as
    the edit made it quieter: samples are stored at one peak, so a chord
    (a sum of notes, a lower RMS for the same peak) is made up for in the
    gain, where there is room. `dB` is what is still missing when the gain
    reached its limit, 0.0 normally."""
    import math
    from . import splice
    asked = {k: v for k, v in edits.items() if k in EDITS and v}
    out = patch.clone()
    if not asked:
        return out, 0.0
    out.params = {**out.params, **asked}
    lo, hi, per = out.bands
    mid = (lo + hi) // 2
    group = next((g for g in groups(out.bands) if g[0] <= mid <= g[1]),
                 groups(out.bands)[0])
    plain = make_sample(out.family, patch.params, *group)
    made = make_sample(out.family, out.params, *group)
    ratio = splice.level_ratio(plain, made, is_pitched(out.family, out.params),
                               (group[0] + group[1]) // 2)
    gain = out.inst.get("gain", 128)
    wanted = gain * ratio
    new = max(2, min(128, int(round(wanted / 2.0)) * 2))
    out.inst["gain"] = new
    short = 20.0 * math.log10(wanted / new) if wanted > new * 1.03 else 0.0
    return out, round(short, 1)


def groups(bands, want: Optional[Tuple[int, int]] = None):
    """[(low, high)] — the notes each sample serves. `want` keeps only the
    groups that include a note in `want[0]..want[1]`."""
    lo, hi, per = bands
    out = []
    low = lo
    while low <= hi:
        high = min(hi, low + per - 1)
        if want is None or (high >= want[0] and low <= want[1]):
            out.append((low, high))
        low = high + 1
    return out


def clear_cache():
    _CACHE.clear()


# -- the patch ------------------------------------------------------------------
@dataclass
class Patch:
    family: str
    params: Dict[str, Any] = field(default_factory=dict)
    #: volume envelope, [[seconds, level 0..1], ...]; sustain/loop are
    #: (first, last) node indices
    amp: List[list] = field(default_factory=lambda: [[0.0, 1.0]])
    amp_sustain: Optional[list] = None
    amp_loop: Optional[list] = None
    #: the filter: cutoff and resonance 0..127, and an envelope that scales
    #: the cutoff, [[seconds, value 0..64], ...]
    cutoff: Optional[int] = None
    resonance: Optional[int] = None
    filter_env: Optional[list] = None
    filter_sustain: Optional[list] = None
    filter_loop: Optional[list] = None
    #: pan envelope [[seconds, -32..32]], pitch envelope [[seconds, -32..32]]
    #: in half-semitones (the file has one slot for pitch and filter)
    pan_env: Optional[list] = None
    pan_loop: Optional[list] = None
    pitch_env: Optional[list] = None
    #: nna cut|cont|off|fade, dct, dca, fade 0..256, gain 0..128, pan 0..64,
    #: rv, rp, pps, ppc, vol 0..64
    inst: Dict[str, Any] = field(default_factory=dict)
    #: sample auto-vibrato: [speed, depth, rate, wave]
    vib: Optional[list] = None
    #: first note, last note (1-based IT notes) and notes per sample
    bands: Tuple[int, int, int] = (13, 96, 12)

    def clone(self, **changes) -> "Patch":
        out = copy.deepcopy(self)
        for key, value in changes.items():
            setattr(out, key, value)
        return out

    # -- files -------------------------------------------------------------------
    def to_dict(self) -> dict:
        data = {"family": self.family, "params": copy.deepcopy(self.params),
                "amp": [[round(t, 5), round(v, 4)] for t, v in self.amp]}
        for name in ("amp_sustain", "amp_loop", "filter_sustain",
                     "filter_loop", "pan_loop"):
            if getattr(self, name) is not None:
                data[name] = list(getattr(self, name))
        if self.cutoff is not None:
            data["cutoff"] = self.cutoff
        if self.resonance is not None:
            data["resonance"] = self.resonance
        for name in ("filter_env", "pan_env", "pitch_env"):
            value = getattr(self, name)
            if value is not None:
                data[name] = [[round(t, 5), v] for t, v in value]
        if self.inst:
            data["inst"] = dict(self.inst)
        if self.vib is not None:
            data["vib"] = list(self.vib)
        data["bands"] = list(self.bands)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Patch":
        try:
            patch = cls(family=data["family"],
                        params=copy.deepcopy(data.get("params", {})),
                        amp=[list(n) for n in data.get("amp", [[0.0, 1.0]])])
        except (KeyError, TypeError) as error:
            raise PatchError(f"a patch needs 'family' ({error})") from None
        for name in ("amp_sustain", "amp_loop", "filter_sustain",
                     "filter_loop", "pan_loop"):
            if data.get(name) is not None:
                setattr(patch, name, list(data[name]))
        patch.cutoff = data.get("cutoff")
        patch.resonance = data.get("resonance")
        for name in ("filter_env", "pan_env", "pitch_env"):
            if data.get(name) is not None:
                setattr(patch, name, [list(n) for n in data[name]])
        patch.inst = dict(data.get("inst", {}))
        patch.vib = list(data["vib"]) if data.get("vib") is not None else None
        patch.bands = tuple(data.get("bands", (13, 96, 12)))
        return patch

    # -- legality ------------------------------------------------------------------
    def problems(self) -> List[str]:
        """Sentences naming each way this patch cannot be written to a .it
        file or would not play as written. [] when it is fine."""
        out = []

        def check(label, value, low, high):
            if not isinstance(value, (int, float)) or isinstance(value, bool) \
                    or value != value or not low <= value <= high:
                out.append(f"{label} is {value!r}; the format takes "
                           f"{low}..{high}")

        def nodes(label, env, low, high, marks=()):
            if env is None:
                return
            if not 1 <= len(env) <= MAX_NODES:
                out.append(f"{label} has {len(env)} nodes; it holds "
                           f"1..{MAX_NODES}")
                return
            if abs(env[0][0]) > 1e-9:
                out.append(f"{label} must start at 0 s, not {env[0][0]}")
            for (t0, _), (t1, _) in zip(env, env[1:]):
                if t1 <= t0:
                    out.append(f"{label} times must increase")
                    break
            for _, v in env:
                check(f"{label} value", v, low, high)
            for mark in marks:
                if mark is not None and (len(mark) != 2 or not
                                         0 <= mark[0] <= mark[1] < len(env)):
                    out.append(f"{label} loop/sustain {mark!r} is not a pair "
                               f"of node indices inside 0..{len(env) - 1}")

        nodes("amp", self.amp, 0.0, 1.0, (self.amp_sustain, self.amp_loop))
        nodes("filter_env", self.filter_env, 0, 64,
              (self.filter_sustain, self.filter_loop))
        nodes("pan_env", self.pan_env, -32, 32, (self.pan_loop,))
        nodes("pitch_env", self.pitch_env, -32, 32)
        if self.filter_env and self.pitch_env:
            out.append("a filter envelope and a pitch envelope share one "
                       "slot in the file; give the patch one of them")
        if self.cutoff is not None:
            check("cutoff", self.cutoff, 0, 127)
        if self.resonance is not None:
            check("resonance", self.resonance, 0, 127)
        i = self.inst
        for key, low, high in (("fade", 0, 256), ("gain", 0, 128),
                               ("pan", 0, 64), ("rv", 0, 100), ("rp", 0, 64),
                               ("pps", -32, 32), ("vol", 0, 64)):
            if key in i and i[key] is not None:
                check(key, i[key], low, high)
        for key, names in (("nna", _NNA), ("dct", _DCT), ("dca", _DCA)):
            if key in i and i[key] not in names:
                out.append(f"{key} is {i[key]!r}; use one of "
                           f"{', '.join(names)}")
        if i.get("gain", 128) is not None and 0 <= i.get("gain", 128) < 2:
            out.append("gain under 2 plays silent (the player applies it in "
                       "steps of 2)")
        if self.vib is not None:
            if len(self.vib) != 4:
                out.append("vib is [speed, depth, rate, wave]")
            else:
                for label, v, hi in zip(("vib speed", "vib depth",
                                         "vib rate", "vib wave"),
                                        self.vib, (64, 64, 64, 3)):
                    check(label, v, 0, hi)
        lo, hi, per = self.bands
        check("first note", lo, 1, 120)
        check("last note", hi, lo if isinstance(lo, int) else 1, 120)
        check("notes per sample", per, 1, 120)
        return out


_NNA = {"cut": 0, "cont": 1, "off": 2, "fade": 3}
_DCT = {"off": 0, "note": 1, "sample": 2, "inst": 3}
_DCA = {"cut": 0, "off": 1, "fade": 2}


# -- seconds to ticks -----------------------------------------------------------
def to_ticks(nodes, tick: float, scale: float, low: int, high: int,
             marks=()):
    """[[seconds, value]] -> ([(tick, int value)], remapped marks).

    Times round to the nearest tick; nodes that land on the same tick merge
    (the later one wins, which is what a ramp too short for a tick should
    do: be instant). Marks are node indices and follow the merge.
    """
    out: List[list] = []
    index_of = []
    for t, v in nodes:
        k = int(round(t / tick))
        value = int(round(v * scale))
        value = max(low, min(high, value))
        if out and out[-1][0] == k:
            out[-1][1] = value
        else:
            out.append([k, value])
        index_of.append(len(out) - 1)
    mapped = []
    for mark in marks:
        mapped.append(None if mark is None
                      else (index_of[mark[0]], index_of[mark[1]]))
    return [(k, v) for k, v in out], mapped


def build_envelopes(patch: Patch, tick: float):
    """-> (volume, pan, pitch_or_filter) as model.Envelope or None."""
    volume = None
    if patch.amp and not (len(patch.amp) == 1 and patch.amp[0][1] >= 1.0
                          and patch.amp_sustain is None
                          and patch.amp_loop is None):
        nodes, (sus, loop) = to_ticks(patch.amp, tick, 64.0, 0, 64,
                                      (patch.amp_sustain, patch.amp_loop))
        volume = M.Envelope(nodes=nodes, sustain=sus, loop=loop)
    pan = None
    if patch.pan_env:
        nodes, (loop,) = to_ticks(patch.pan_env, tick, 1.0, -32, 32,
                                  (patch.pan_loop,))
        pan = M.Envelope(nodes=nodes, loop=loop)
    pitch = None
    if patch.filter_env:
        nodes, (sus, loop) = to_ticks(patch.filter_env, tick, 1.0, 0, 64,
                                      (patch.filter_sustain,
                                       patch.filter_loop))
        pitch = M.Envelope(nodes=nodes, sustain=sus, loop=loop, filter=True)
    elif patch.pitch_env:
        nodes, _ = to_ticks(patch.pitch_env, tick, 1.0, -32, 32)
        pitch = M.Envelope(nodes=nodes)
    return volume, pan, pitch


def build_instrument(patch: Patch, tick: float, name: str = "") -> M.Instrument:
    """The instrument without its samples (the note map is empty)."""
    ins = M.Instrument(name=name[:25])
    i = patch.inst
    ins.nna = _NNA[i.get("nna", "cut")]
    ins.dct = _DCT[i.get("dct", "off")]
    ins.dca = _DCA[i.get("dca", "cut")]
    ins.fadeout = int(i.get("fade", 0))
    ins.global_volume = int(i.get("gain", 128))
    if i.get("pan") is not None:
        ins.default_pan = int(i["pan"])
    ins.random_volume = int(i.get("rv", 0))
    ins.random_pan = int(i.get("rp", 0))
    ins.pitch_pan_separation = int(i.get("pps", 0))
    if i.get("ppc") is not None:
        ins.pitch_pan_center = int(i["ppc"])
    ins.cutoff = patch.cutoff
    ins.resonance = patch.resonance
    if patch.filter_env and ins.cutoff is None:
        ins.cutoff = 127                     # the envelope needs the filter on
    (ins.volume_envelope, ins.pan_envelope,
     ins.pitch_envelope) = build_envelopes(patch, tick)
    return ins


def install(patch: Patch, module: M.Module, number: int = 1,
            tick: float = REFERENCE_TICK, name: str = "",
            want: Optional[Tuple[int, int]] = None) -> int:
    """Write the patch into `module` as instrument `number`, appending its
    samples. `want` = (first note, last note) builds only the samples that
    serve those notes (a probe needs the one it plays). -> the number."""
    problems = patch.problems()
    if problems:
        raise PatchError("; ".join(problems[:3]))
    ins = build_instrument(patch, tick, name or patch.family)
    vib = patch.vib
    volume = int(patch.inst.get("vol", 64))
    for low, high in groups(patch.bands, want):
        built = make_sample(patch.family, patch.params, low, high)
        sample = M.Sample(
            name=(built.name or name or patch.family)[:25],
            data=built.data, right=getattr(built, "right", None),
            c5speed=built.c5speed, loop=built.loop, volume=volume)
        if vib:
            (sample.vibrato_speed, sample.vibrato_depth, sample.vibrato_rate,
             sample.vibrato_wave) = vib
        module.samples.append(sample)
        ins.use_sample(len(module.samples), low, high)
    while len(module.instruments) < number:
        module.instruments.append(M.Instrument(
            name="", note_map=[(n, 0) for n in range(120)]))
    module.instruments[number - 1] = ins
    return number


def built_samples(patch: Patch, want: Optional[Tuple[int, int]] = None):
    """[(low, high, Built)] for the patch (cached)."""
    return [(lo, hi, make_sample(patch.family, patch.params, lo, hi))
            for lo, hi in groups(patch.bands, want)]


def sample_bytes(patch: Patch) -> int:
    """How much sample data the patch writes, in bytes (16-bit)."""
    return sum(2 * len(b.data) * getattr(b, "channels", 1)
               for _, _, b in built_samples(patch))
