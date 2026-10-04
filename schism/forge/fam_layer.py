"""fam_layer.py — a stack of instruments: PCM spliced by role.

A tracker instrument is a stack, not an average. Blending two sounds by
their dials (`mixing`, mode `dna`) compiles a third sound from the average
and keeps nothing of either: a kick and a bass become a dull bass. A stack
keeps both, because it takes the **samples** and puts each where it belongs
in time:

    click    the first 30 ms of a sound, at a fixed gain, then a handover to
             the body (a kick's knock on a bass, a clap's crack on a snare)
    body     the sound that holds the pitch, the loop and the volume
             envelope; it comes in at 20 ms across 15 ms when there is a click
    noise    a sound's top end, high-passed, laid over the whole hit
    sub      a sound's bottom, low-passed, laid under it
    tail     a sound that follows the body, its level set from the body's
             RMS; looped, it is the loop of the stack (a kick, then a pad)

The stack is one sample for each band of keys, like any forged instrument:
every part is made for the same band, converted to the rate the body plays
at, edited by its role and summed. A drum is kept at its recorded pitch
whatever key is pressed (the band's centre plays it as recorded); a pitched
part follows the key. A body that loops keeps its loop whole: the front
that plays once is lengthened by whole repeats of the loop until the click
and everything else fits in it (`splice.extend_intro`), which changes
nothing that is heard.

Only one part of a stack can loop, because a sample has one loop: a looped
body, or, if the body is a one-shot, a looped tail. The instrument's volume
envelope, NNA, filter and the rest are the loop-owner's.
"""

import copy
import math

from . import splice, spectral
from .dna import AXES
from .family import Compiled, Family, get_family, register_family
from .genome import Genome
from .patch import (HANDLES, Patch, PatchError, groups, is_pitched,
                    make_sample, register_sampler, with_edits)

ROLES = ("click", "body", "noise", "sub", "tail")
ALIASES = {"transient": "click", "attack": "click", "crack": "click",
           "snap": "click", "tone": "body", "main": "body", "low": "sub",
           "bottom": "sub", "air": "noise", "hiss": "noise", "ring": "tail",
           "wash": "tail"}
#: per role: option -> (least, most, default); times in ms, levels in dB,
#: filters in Hz. A default of None is worked out (see `_tail_start`)
OPTIONS = {
    "click": {"ms": (2.0, 400.0, 30.0), "fade": (1.0, 200.0, 15.0)},
    "body": {"from": (0.0, 400.0, 20.0)},
    "noise": {"hp": (100.0, 12000.0, 1500.0), "ms": (5.0, 3000.0, 400.0),
              "at": (0.0, 3000.0, 0.0)},
    "sub": {"lp": (30.0, 800.0, 150.0), "ms": (5.0, 3000.0, 500.0),
            "at": (0.0, 3000.0, 0.0)},
    "tail": {"at": (0.0, 3000.0, None), "level": (-40.0, 12.0, -9.0),
             "ms": (50.0, 6000.0, 1500.0)},
}
COMMON = {"db": (-40.0, 24.0, 0.0)}
MAX_PARTS = 6
#: noise-like drums, which a second unpitched part is taken to be
NOISY_KINDS = ("hat", "clap", "shaker", "cymbal", "rim", "riser", "swoosh")
#: a click's hand-over is never shorter than this, ms
MIN_FADE_MS = 1.0
#: the end of a part laid over a body is faded this long, ms
END_FADE_MS = 5.0


def role_of(word: str) -> str:
    role = ALIASES.get(str(word).strip().lower(), str(word).strip().lower())
    if role not in ROLES:
        raise PatchError(f"{word!r} is not a role; the roles are "
                         f"{', '.join(ROLES)}")
    return role


# -- the parts ----------------------------------------------------------------------
def parse_part(text: str):
    """'clap@click/ms=40/db=-3' -> ('clap', 'click', {'ms': '40', 'db': '-3'});
    the role is None when the text does not give one. The name may carry a
    colon (`archetype:kick`, `starter:punch_bass`)."""
    pieces = text.strip().split("/")
    name, role = pieces[0], None
    if "@" in name:
        name, role = name.rsplit("@", 1)
    if not name:
        raise PatchError("a part needs a name")
    options = {}
    for piece in pieces[1:]:
        if "=" not in piece:
            raise PatchError(f"{piece!r} is not key=value; write "
                             f"clap@click/ms=40/db=-3")
        key, value = piece.split("=", 1)
        options[key.strip().lower()] = value.strip()
    return name.strip(), (role.strip() if role else None), options


def make_part(role: str, patch: Patch, name: str = "", style=None,
              **options) -> dict:
    """One part of a stack, as the JSON a recipe holds: its role, the
    options of that role, the patch whose sample it takes and the few facts
    about how that sound is played (`kind`, `release_expected`) that the
    stack keeps if it is the one that holds the loop."""
    role = role_of(role)
    part = {"role": role, "name": name or patch.family,
            "patch": patch.to_dict()}
    kept = {k: v for k, v in (style or {}).items()
            if k in ("kind", "release_expected")}
    if kept:
        part["style"] = kept
    for key, value in options.items():
        if value is None:
            continue
        table = {**COMMON, **OPTIONS[role]}
        if key not in table:
            raise PatchError(
                f"{key} is not an option of the {role} role; it takes "
                f"{', '.join(sorted(table))}")
        low, high, _ = table[key]
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise PatchError(f"{role} {key}={value!r} is not a number") \
                from None
        if not low <= number <= high:
            raise PatchError(f"{role} {key}={number:g} is outside "
                             f"{low:g}..{high:g}")
        part[key] = number
    return part


def option(part: dict, key: str):
    table = {**COMMON, **OPTIONS[part["role"]]}
    value = part.get(key)
    return table[key][2] if value is None else value


def problems(params: dict) -> list:
    """Sentences naming what is wrong with the recipe of a stack."""
    out = []
    parts = params.get("parts")
    if not isinstance(parts, list) or not parts:
        return ["a stack needs `parts`"]
    if len(parts) > MAX_PARTS:
        out.append(f"{len(parts)} parts; a stack holds at most {MAX_PARTS}")
    roles = [p.get("role") for p in parts]
    for p in parts:
        if p.get("role") not in ROLES:
            out.append(f"{p.get('role')!r} is not a role "
                       f"({', '.join(ROLES)})")
        if not isinstance(p.get("patch"), dict) or "family" not in p["patch"]:
            out.append(f"the {p.get('role')} part has no patch")
    if roles.count("body") != 1:
        out.append(f"a stack needs exactly one body (it holds the pitch and "
                   f"the envelope); this has {roles.count('body')}")
    if roles.count("tail") > 1:
        out.append("a stack takes one tail")
    for role in ("click", "noise", "sub"):
        if roles.count(role) > 3:
            out.append(f"at most three {role} parts")
    return out


# -- what the parts are, measured by building them ------------------------------------
def _pitched(patch: Patch) -> bool:
    return is_pitched(patch.family, patch.params)


class _Voice:
    """One part, built for one band and put into floats."""

    def __init__(self, part: dict, low: int, high: int, base: int):
        self.part = part
        self.role = part["role"]
        self.patch = Patch.from_dict(part["patch"])
        self.pitched = _pitched(self.patch)
        self.built = make_sample(self.patch.family, self.patch.params,
                                 low, high)
        self.rate = splice.native_rate(self.built, base, self.pitched)
        self.loop = self.built.loop
        self.gain = (self.patch.inst.get("gain", 128) / 128.0
                     * 10.0 ** (option(part, "db") / 20.0))
        self.channels = [splice.floats(self.built.data)]
        if self.built.right is not None:
            self.channels.append(splice.floats(self.built.right))
        if self.loop is not None:
            # a loop ends where its data does; anything after is never played
            end = self.loop[1]
            self.channels = [x[:end] for x in self.channels]

    def restereo(self, cents: float, low: int, high: int):
        """The same part as a stereo pair `cents` either side (the family
        builds it: a loop can only be widened in its spectrum)."""
        self.built = make_sample(self.patch.family,
                                 {**self.patch.params, "stereo": cents},
                                 low, high)
        self.channels = [splice.floats(self.built.data),
                         splice.floats(self.built.right)]
        end = self.built.loop[1]
        self.channels = [x[:end] for x in self.channels]
        self.loop = self.built.loop


def _owner(voices):
    body = next(v for v in voices if v.role == "body")
    tail = next((v for v in voices if v.role == "tail"), None)
    if body.loop is not None and tail is not None and tail.loop is not None:
        raise PatchError(
            "the body and the tail both loop, and a sample has one loop; "
            "make one of them a one-shot (a click, noise or sub keeps only "
            "its first moments), or mix them (same family) instead")
    if body.loop is None and tail is not None and tail.loop is not None:
        return tail
    return body


def _frames(ms: float, rate: float) -> int:
    return max(0, int(round(ms * 0.001 * rate)))


def _crossfade(part: dict, from_ms: float, rate: float):
    """-> (t0, f, n): a click's hand-over starts at frame t0, takes f
    frames, and the click audio is n frames long."""
    n = max(2, _frames(option(part, "ms"), rate))
    f_min = _frames(MIN_FADE_MS, rate)
    t0 = min(_frames(from_ms, rate), max(0, n - f_min))
    f = max(f_min, min(_frames(option(part, "fade"), rate), n - t0))
    return t0, f, min(n, t0 + f)


def _tail_start(body, rate: float) -> int:
    """Frame at which a tail comes in when the score does not say: the body
    has fallen to a third of its loudest 5 ms, or, for a body that holds,
    a twentieth of a second in."""
    x = body
    width = max(1, _frames(5.0, rate))
    env = []
    for i in range(0, len(x) - width, width):
        env.append(max(abs(v) for v in x[i:i + width]))
    if not env:
        return 0
    top = max(env)
    peak_at = env.index(top)
    for i in range(peak_at, len(env)):
        if env[i] < 0.35 * top:
            return max(_frames(30.0, rate), min(i * width, _frames(1200.0, rate)))
    return _frames(50.0, rate)


def _window(x, n: int, fade_ms: float, rate: float):
    out = list(x[:n])
    splice.fade_out(out, min(len(out), _frames(fade_ms, rate)))
    return out


def _gate(x, t0: int, f: int):
    """The front of a body that has clicks over it: silent until `t0`, then
    rising to full over `f` frames (the click falls as the cosine, the body
    rises as the sine, so the hand-over keeps its power)."""
    for i in range(min(len(x), t0)):
        x[i] = 0.0
    for i in range(t0, min(len(x), t0 + f)):
        x[i] *= math.sin(0.5 * math.pi * (i - t0 + 0.5) / f)


def stack_sampler(params: dict, low: int, high: int):
    """One sample serving notes `low`..`high`: see the module doc."""
    base = (low + high) // 2
    voices = [_Voice(p, low, high, base) for p in params["parts"]]
    owner = _owner(voices)
    body = next(v for v in voices if v.role == "body")
    tail = next((v for v in voices if v.role == "tail"), None)
    cents = float(params.get("stereo") or 0.0)
    if cents and owner.loop is not None and len(owner.channels) == 1:
        owner.restereo(cents, low, high)
    limit = splice.band_limit(low, high, base, owner.pitched)

    # -- the rate the stack is built at: the owner's, or more if that is slow ----------
    k = 1
    while owner.rate * k < splice.MIN_RATE:
        k += 1
    dom = owner.rate * k
    channels = owner.channels
    loop = owner.loop
    if k > 1:
        wrap = loop[0] if loop else None
        channels = [splice.resample(x, float(k), wrap) for x in channels]
        loop = (loop[0] * k, loop[1] * k) if loop else None

    def one_shot(voice):
        """A part that is not the owner, as a one-shot at `dom`."""
        chans = voice.channels
        if voice.loop is not None:
            chans = splice.unroll(chans, voice.loop, max(2, _frames(
                option(voice.part, "ms"), voice.rate)))
        return [splice.convert(x, voice.rate, dom, limit) for x in chans]

    # -- the body, the clicks over it, the hand-over between them ------------------------
    body_chans = channels if body is owner else one_shot(body)
    from_ms = option(body.part, "from")
    geometry = [(v,) + _crossfade(v.part, from_ms, dom)
                for v in voices if v.role == "click"]
    gate = None
    if geometry:
        _, t0, f, _ = max(geometry, key=lambda g: g[1] + g[2])
        gate = (t0, f)
    seen = [v * body.gain for v in body_chans[0]]       # the body as heard
    if gate:
        _gate(seen, *gate)
    body_rms = splice.level(seen, dom) if seen else 0.0

    adds = []                            # (start frame, channels, gain)
    for v, t0, f, n in geometry:
        chans = [list(x[:n]) for x in one_shot(v)]
        for x in chans:
            for i in range(t0, len(x)):
                x[i] *= math.cos(0.5 * math.pi * min(1.0, (i - t0 + 0.5) / f))
        adds.append((0, chans, v.gain))

    # -- noise, sub ----------------------------------------------------------------------
    for v in voices:
        if v.role == "noise":
            chans = [splice.highpass(x, option(v.part, "hp"), dom)
                     for x in one_shot(v)]
        elif v.role == "sub":
            chans = [splice.lowpass(x, option(v.part, "lp"), dom)
                     for x in one_shot(v)]
        else:
            continue
        n = _frames(option(v.part, "ms"), dom)
        adds.append((_frames(option(v.part, "at"), dom),
                     [_window(x, n, END_FADE_MS, dom) for x in chans],
                     v.gain))

    # -- the tail: its level comes from the body ---------------------------------------------
    owner_gain = owner.gain
    shift = 0
    if tail is not None:
        at = tail.part.get("at")
        start = _frames(at, dom) if at is not None else _tail_start(seen, dom)
        chans = channels if tail is owner else one_shot(tail)
        r_tail = splice.level(chans[0], dom)
        aim = body_rms * 10.0 ** ((option(tail.part, "level")
                                   + option(tail.part, "db")) / 20.0)
        gain = aim / r_tail if r_tail > 0.0 else 0.0
        if tail is owner:
            owner_gain, shift = gain, start
        else:
            n = _frames(option(tail.part, "ms"), dom)
            chans = [_window(x, n, 4 * END_FADE_MS, dom) for x in chans]
            for x in chans:
                splice.fade_in(x, _frames(END_FADE_MS, dom))
            adds.append((start, chans, gain))
    if tail is owner:
        adds.append((0, body_chans, body.gain))        # a one-shot body

    # -- everything must end before a loop begins -------------------------------------------
    if shift:
        channels = [[0.0] * shift + x for x in channels]
        if loop:
            loop = (loop[0] + shift, loop[1] + shift)
    need = max((s + len(chans[0]) for s, chans, _ in adds), default=0)
    if gate:
        need = max(need, gate[0] + gate[1])
    if shift:
        need = max(need, shift + _frames(END_FADE_MS, dom))
    if loop is not None:
        channels, loop = splice.extend_intro(channels, loop, need)
    total = len(channels[0]) if loop is not None else max(len(channels[0]),
                                                          need)
    # the front of the owner is edited only now, past the loop's start
    if shift:
        for x in channels:
            tmp = x[shift:shift + _frames(END_FADE_MS, dom)]
            splice.fade_in(tmp, len(tmp))
            x[shift:shift + len(tmp)] = tmp
    if gate and body is owner:
        for x in channels:
            _gate(x, *gate)
    elif gate:
        for x in body_chans:
            _gate(x, *gate)

    # -- the sum --------------------------------------------------------------------------------
    two = len(channels) == 2 or any(len(c) == 2 for _, c, _ in adds)
    yl = [0.0] * total
    yr = [0.0] * total if two else None

    def put(chans, start, gain):
        left = chans[0]
        right = chans[1] if len(chans) == 2 else chans[0]
        for i in range(min(len(left), total - start)):
            yl[start + i] += left[i] * gain
        if yr is not None:
            for i in range(min(len(right), total - start)):
                yr[start + i] += right[i] * gain

    put(channels, 0, owner_gain)
    for start, chans, gain in adds:
        put(chans, start, gain)

    # -- a one-shot stack is widened at the end; a looped one was, in its spectrum ----------------
    if cents and loop is None and not two:
        yl, yr = splice.double(yl, cents)
        two = True
    scale = 128.0 / float(params.get("gain", 128))
    top = splice.peak(yl, *([yr] if two else [])) * scale
    fit = min(1.0, splice.CEILING / top) if top > 0.0 else 1.0
    packed = splice.pack([yl] + ([yr] if two else []), scale * fit)
    return spectral.Built(packed[0], owner.built.c5speed * k, loop,
                          f"layer {low}-{high}",
                          right=packed[1] if two else None)


register_sampler("layer", stack_sampler, handles=("stereo",))


# -- building a stack ------------------------------------------------------------------
class Source:
    """A part before it is in a stack: a compiled instrument, where it came
    from, the role it is to play (None: decided by `assign_roles`) and the
    options of that role."""

    def __init__(self, name, compiled, genome=None, role=None, options=None,
                 weight=1.0):
        self.name = name
        self.compiled = compiled
        self.genome = genome
        self.role = role_of(role) if role else None
        self.options = dict(options or {})
        self.weight = float(weight)


def _mid_group(patch: Patch):
    mid = (patch.bands[0] + patch.bands[1]) // 2
    every = groups(patch.bands)
    return next((g for g in every if g[0] <= mid <= g[1]), every[0])


def measure_part(patch: Patch):
    """-> (seconds, looped): how long the part's sample is, for the middle
    of its range, and whether it loops."""
    low, high = _mid_group(patch)
    built = make_sample(patch.family, patch.params, low, high)
    rate = splice.native_rate(built, (low + high) // 2, _pitched(patch))
    return len(built.data) / rate, built.loop is not None


def assign_roles(sources):
    """Give each source without a role one: the sound that loops, or failing
    that the longest pitched one, is the body; of the others the shortest is
    the click, a noisy drum is noise, and what is left is sub."""
    info = [measure_part(s.compiled.patch) for s in sources]
    free = [i for i, s in enumerate(sources) if s.role is None]
    if free and not any(s.role == "body" for s in sources):
        loops = [i for i in free if info[i][1]]
        if len(loops) > 1:
            raise PatchError(
                "more than one of these sounds loops, and a sample has one "
                "loop: say which is the body and which gives only its first "
                "moments (NAME@click), or mix them if they share a family")
        if loops:
            body = loops[0]
        else:
            pitched = [i for i in free
                       if _pitched(sources[i].compiled.patch)]
            body = max(pitched or free, key=lambda i: info[i][0])
        sources[body].role = "body"
        free.remove(body)
    for i in sorted(free, key=lambda i: info[i][0]):
        used = {s.role for s in sources if s.role}
        kind = sources[i].compiled.style.get("kind", "")
        if "click" not in used:
            sources[i].role = "click"
        elif kind in NOISY_KINDS and "noise" not in used:
            sources[i].role = "noise"
        elif "sub" not in used:
            sources[i].role = "sub"
        else:
            sources[i].role = "noise"
    return sources


def describe(parts) -> str:
    """'clap as click (30 ms), snare as body (from 20 ms)'."""
    out = []
    for part in parts:
        role = part["role"]
        if role == "click":
            detail = f"{option(part, 'ms'):g} ms"
        elif role == "body":
            detail = f"from {option(part, 'from'):g} ms"
        elif role == "noise":
            detail = f"over {option(part, 'hp'):g} Hz"
        elif role == "sub":
            detail = f"under {option(part, 'lp'):g} Hz"
        else:
            detail = f"{option(part, 'level'):+g} dB"
        out.append(f"{part['name']} as {role} ({detail})")
    return ", ".join(out)


def assemble(parts, name: str = "layer", edits=None):
    """-> Compiled, from parts already made (`make_part`). The instrument
    around the stack (envelopes, NNA, filter, bands) is the loop-owner's;
    its gain is set so that the stored samples sit at full scale."""
    bad = problems({"parts": parts})
    if bad:
        raise PatchError("; ".join(bad))
    patches = [Patch.from_dict(p["patch"]) for p in parts]
    measured = [measure_part(pt) for pt in patches]
    roles = [p["role"] for p in parts]
    ib = roles.index("body")
    it = roles.index("tail") if "tail" in roles else None
    if measured[ib][1] and it is not None and measured[it][1]:
        raise PatchError(
            "the body and the tail both loop, and a sample has one loop; "
            "make one of them a one-shot, or mix them (same family)")
    owner_at = it if (not measured[ib][1] and it is not None
                      and measured[it][1]) else ib
    owner = patches[owner_at]
    looped = measured[owner_at][1]
    pitched = _pitched(owner)
    edits = {k: v for k, v in (edits or {}).items() if v}
    if edits.get("stereo") and looped \
            and "stereo" not in HANDLES.get(owner.family, ()):
        raise PatchError(
            f"stereo on a stack whose loop is a {owner.family} sound: only a "
            f"tone can be widened and keep its loop; widen a tone, or use a "
            f"body that does not loop")
    params = {"parts": parts, "pitched": pitched, "looped": looped,
              "gain": 128}
    if not pitched and owner.params.get("reg_note") is not None:
        params["reg_note"] = owner.params["reg_note"]
    patch = owner.clone()
    patch.family = "layer"
    patch.params = params
    low, high = _mid_group(patch)
    mid = make_sample("layer", params, low, high)
    top = max((abs(v) for v in mid.data), default=0)
    if top == 0:
        raise PatchError("the stack renders as silence")
    gain = int(math.ceil(128.0 * (top / 32767.0) / splice.PEAK / 2.0)) * 2
    gain = max(2, min(128, gain))
    params["gain"] = gain
    patch.inst["gain"] = gain
    short = 0.0
    if edits:
        patch, short = with_edits(patch, edits)
    problems_ = patch.problems()
    if problems_:
        raise PatchError("; ".join(problems_[:3]))
    style = dict(parts[owner_at].get("style") or {})
    style.update({"pitched": pitched, "layers": [p["role"] for p in parts]})
    style.setdefault("release_expected", pitched)
    notes = [describe(parts)]
    notes.append("the loop, envelope and instrument switches are the "
                 f"{roles[owner_at]}'s ({patches[owner_at].family})")
    if looped:
        notes.append("the front that plays once was lengthened by whole "
                     "repeats of the loop to hold the other parts")
    if edits:
        notes.append("edits: " + ", ".join(
            f"{k}={v}" for k, v in sorted(edits.items())))
    if short:
        notes.append(f"{short:.1f} dB quieter than the unedited stack even at "
                     f"full instrument gain")
    if owner.amp and owner.amp[0][1] < 0.5 and len(parts) > 1:
        notes.append(f"the {roles[owner_at]} fades in through its volume "
                     f"envelope, and what is stacked with it fades in too: "
                     f"choose one with a faster attack, or write the "
                     f"envelope yourself (venv=)")
    return Compiled("layer", name, patch, {}, style, notes)


def build_stack(sources, name: str = "layer", edits=None, register=None):
    """-> (Genome, Compiled) for a list of `Source`. Roles not given are
    assigned. A `chord` edit goes to the body (a click is not a chord); the
    other edits are of the whole stack."""
    if len(sources) < 2:
        raise PatchError("a stack needs at least two parts")
    assign_roles(sources)
    top = max(s.weight for s in sources)
    edits = dict(edits or {})
    chord = edits.pop("chord", None)
    parts = []
    for s in sources:
        patch = s.compiled.patch
        options = dict(s.options)
        if s.role == "body" and chord:
            patch, _ = with_edits(patch, {"chord": chord})
        if "db" not in options and s.weight != top:
            options["db"] = round(20.0 * math.log10(s.weight / top), 2)
        parts.append(make_part(s.role, patch, s.name, s.compiled.style,
                               **options))
    compiled = assemble(parts, name, edits)
    body = next(s for s in sources if s.role == "body")
    if chord:
        compiled.notes.append(f"the body is a chord: {chord}")
    owner = body.genome
    reg = register or (owner.register if owner is not None
                       else get_family("layer").default_register)
    dna = owner.dna if owner is not None else None
    from .dna import DNA
    genome = Genome("layer", dna or DNA(), style={"parts": parts,
                                                  "edits": {**edits,
                                                            **({"chord": chord}
                                                               if chord else {})}},
                    name=name, register=tuple(reg),
                    lineage=["layer:" + " + ".join(
                        f"{s.name}@{s.role}" for s in sources)])
    return genome, compiled


# -- the family ----------------------------------------------------------------------------
class StackFamily(Family):
    name = "layer"
    doc = ("a stack of other instruments, PCM spliced by role: click, body, "
           "noise, sub, tail")
    default_register = ("A", 3)
    genes: dict = {}
    supports = frozenset(AXES)
    pitched = True

    def compile(self, genome: Genome) -> Compiled:
        parts = genome.style.get("parts")
        if not parts:
            raise KeyError("a layer genome carries its `parts` in its style")
        edits = genome.style.get("edits") or {}
        edits = dict(edits)
        chord = edits.pop("chord", None)
        if chord:
            parts = copy.deepcopy(parts)
            for part in parts:
                if part["role"] == "body":
                    patch, _ = with_edits(Patch.from_dict(part["patch"]),
                                          {"chord": chord})
                    part["patch"] = patch.to_dict()
        return assemble(parts, genome.name or "layer", edits)

    def validate(self, compiled: Compiled) -> list:
        return compiled.patch.problems() + problems(compiled.patch.params)


register_family(StackFamily())
