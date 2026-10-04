"""recipes.py — what an `inst` and a `smp` line can say, and what it makes.

A score names its sounds instead of attaching them:

    smp thump  wave=kick len=0.30 start=170 end=42
    inst 1 name=Bass   wave=saw oct=1-4 nna=cut cutoff=60 res=40 venv=0:64,40:40,160:0
    inst 2 name=Drums  kit=C-2:thump,D-2:snare,F#2:hat
    inst 3 name=Pad    wave=pad voices=5 detune=14 venv=0:0,60:64,... fade=64
    inst 4 name=Bell   forge=bell dna=brightness:0.8 reg=A4
    inst 5 name=Kit    kit=C-2:@kick,D-2:@snare,F#2:@hat

`compile_instrument` turns one such line into an `Instrument` and the
`Sample`s it needs. The vocabulary is a table (`WAVES`, `ENVELOPES`,
`SETTINGS`) rather than code, because the same table is what the error
message lists when a model gets a key wrong, and what docs/NOTATION.md is
generated from. A key not in the table is an error naming the keys that
are.
"""

import difflib
import json
import math

from . import model as M, synth


class RecipeError(ValueError):
    """A bad key or value in an `inst`/`smp` line. `.fix` says what to write."""

    def __init__(self, message, fix=""):
        super().__init__(message)
        self.fix = fix


# -- value parsers -----------------------------------------------------------
def _int(low, high):
    def parse(text):
        try:
            value = int(text, 0)
        except ValueError:
            raise RecipeError(f"{text!r} is not a whole number") from None
        if not low <= value <= high:
            raise RecipeError(f"{value} is outside {low}..{high}")
        return value
    parse.doc = f"{low}..{high}"
    return parse


def _float(low, high):
    def parse(text):
        try:
            value = float(text)
        except ValueError:
            raise RecipeError(f"{text!r} is not a number") from None
        if not low <= value <= high:
            raise RecipeError(f"{value:g} is outside {low:g}..{high:g}")
        return value
    parse.doc = f"{low:g}..{high:g}"
    return parse


def _choice(*names, aliases=None):
    aliases = aliases or {}

    def parse(text):
        key = text.lower()
        key = aliases.get(key, key)
        if key not in names:
            close = difflib.get_close_matches(key, names, n=1)
            hint = f" — did you mean {close[0]}?" if close else ""
            raise RecipeError(f"{text!r} is not one of {', '.join(names)}"
                              f"{hint}")
        return key
    parse.doc = "|".join(names)
    return parse


def _note(text):
    try:
        number = M.note_number(text)
    except ValueError as error:
        raise RecipeError(str(error)) from None
    if not M.MIN_NOTE <= number <= M.MAX_NOTE:
        raise RecipeError(f"{text!r} is not a note C-0..B-9")
    return number


_note.doc = "a note, C-4"


def _text(text):
    return text.replace("_", " ")


_text.doc = "text, _ for spaces"


def _chord(text):
    from .forge import splice
    try:
        splice.parse_chord(text)
    except splice.SpliceError as error:
        raise RecipeError(str(error)) from None
    return text.strip().lower()


_chord.doc = "a name (min9) or semitones above the key (0,3,7,10,14)"


def _flag(text):
    word = text.strip().lower()
    if word in ("1", "on", "yes", "true"):
        return True
    if word in ("0", "off", "no", "false"):
        return False
    raise RecipeError(f"{text!r} is not on or off")


_flag.doc = "on|off"


def _octaves(text):
    try:
        low, high = (int(p) for p in text.split("-"))
    except ValueError:
        raise RecipeError(f"{text!r} is not an octave range like 1-6") from None
    if not 0 <= low <= high <= 9:
        raise RecipeError(f"octaves are 0..9 and low-high, got {text!r}")
    return low, high


_octaves.doc = "e.g. 1-6"

# -- the waveforms -----------------------------------------------------------
#: kind -> (what it is, {param: (parser, default)})
WAVES = {
    "sine": ("a sine, one cycle per octave sample", {}),
    "saw": ("a band-limited saw, one sample per octave", {}),
    "square": ("a band-limited square", {}),
    "triangle": ("a band-limited triangle", {}),
    "pulse": ("a band-limited pulse of any width",
              {"duty": (_float(0.02, 0.5), 0.25)}),
    "fm": ("two-operator FM: sin(p + index*sin(ratio*p)), one sample per "
           "octave", {"ratio": (_int(1, 16), 2), "index": (_float(0, 12), 3.0),
                      "fb": (_float(0, 4), 0.0)}),
    "pad": ("detuned voices in one long seamless loop",
            {"voices": (_int(1, 9), 5), "detune": (_float(0, 60), 14.0),
             "secs": (_float(0.5, 6), 3.0), "shape": (_choice("saw", "square", "triangle", "pulse"), "saw"),
             "base": (_note, 49), "seed": (_int(0, 9999), 7)}),
    "noise": ("looped noise", {"len": (_float(0.1, 4), 1.0),
                               "color": (_choice("white", "pink", "brown"), "white"),
                               "seed": (_int(0, 9999), 8)}),
    "kick": ("a falling sine with a click", {
        "len": (_float(0.05, 2), 0.34), "start": (_float(40, 400), 165.0),
        "end": (_float(20, 120), 44.0), "sweep": (_float(0.005, 0.3), 0.045),
        "decay": (_float(0.02, 0.8), 0.11), "drive": (_float(0.5, 4), 1.6)}),
    "snare": ("tone plus high-passed noise", {
        "len": (_float(0.05, 1), 0.3), "tone": (_float(80, 400), 185.0),
        "seed": (_int(0, 9999), 2)}),
    "hat": ("metallic squares plus noise, closed", {
        "len": (_float(0.02, 0.5), 0.075), "decay": (_float(0.005, 0.3), 0.022),
        "seed": (_int(0, 9999), 3)}),
    "openhat": ("the same, open", {
        "len": (_float(0.1, 1.5), 0.45), "decay": (_float(0.05, 0.6), 0.14),
        "seed": (_int(0, 9999), 3)}),
    "clap": ("four noise bursts and a tail", {
        "len": (_float(0.05, 1), 0.28), "seed": (_int(0, 9999), 4)}),
    "tom": ("a falling sine; play it at several notes",
            {"len": (_float(0.1, 2), 0.5), "hz": (_float(60, 400), 130.0)}),
    "rim": ("a short two-tone click", {}),
    "pluck": ("Karplus-Strong, tuned", {
        "root": (_note, 61), "len": (_float(0.2, 5), 1.6),
        "damp": (_float(0.9, 0.9999), 0.997), "bright": (_float(500, 18000), 9000.0),
        "seed": (_int(0, 9999), 5)}),
    "bell": ("inharmonic partials, each with its own decay",
             {"len": (_float(0.3, 6), 2.4)}),
}

#: waves that make a loop played at every pitch, as one sample per octave
_MULTISAMPLED = ("sine", "saw", "square", "triangle", "pulse", "fm")
_ONESHOT_LOOPED = ("noise",)

# -- envelopes ---------------------------------------------------------------
#: key -> (slot, value range, doc)
ENVELOPES = {
    "venv": ("volume", (0, 64), "volume 0..64"),
    "penv": ("pan", (-32, 32), "panning -32 (left)..32 (right)"),
    "ienv": ("pitch", (-32, 32), "pitch in half-semitones: 32 is 16 "
                                 "semitones up"),
    "fenv": ("filter", (0, 64), "filter: share of `cutoff` in use, value/64 (64 = as set)"),
}


def parse_envelope(text: str, kind: str):
    """`0:64,10:60,40:40s,80:40s,200:0` -> Envelope.

    Nodes are tick:value. A node ending `s` marks the sustain loop (first
    and last marked), one ending `l` the loop. Ticks are the module's
    ticks, 2.5/tempo seconds each, and must rise from 0.
    """
    slot, (low, high), _ = ENVELOPES[kind]
    nodes, sustain, loop = [], [], []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        mark = ""
        if token[-1] in "sl":
            mark, token = token[-1], token[:-1]
        try:
            tick_text, value_text = token.split(":")
            tick, value = int(tick_text), int(value_text)
        except ValueError:
            raise RecipeError(
                f"{token!r} is not tick:value",
                "write nodes as tick:value, like 0:64,40:30") from None
        if not low <= value <= high:
            raise RecipeError(f"{kind} value {value} is outside {low}..{high}")
        nodes.append((tick, value))
        index = len(nodes) - 1
        if mark == "s":
            sustain.append(index)
        elif mark == "l":
            loop.append(index)
    if not nodes:
        raise RecipeError("an envelope needs at least one node")
    if len(nodes) > 25:
        raise RecipeError(f"{len(nodes)} nodes; an envelope holds 25")
    if nodes[0][0] != 0:
        raise RecipeError(f"the first node must be at tick 0, not {nodes[0][0]}")
    if any(b[0] <= a[0] for a, b in zip(nodes, nodes[1:])):
        raise RecipeError("ticks must increase from node to node")
    for label, marks in (("sustain (s)", sustain), ("loop (l)", loop)):
        if len(marks) not in (0, 1, 2):
            raise RecipeError(f"mark at most two nodes for the {label} "
                              f"loop; one mark makes a loop on that node")
    return M.Envelope(
        nodes=nodes,
        sustain=(sustain[0], sustain[-1]) if sustain else None,
        loop=(loop[0], loop[-1]) if loop else None,
        filter=(kind == "fenv"))


# -- instrument settings -----------------------------------------------------
#: key -> (parser, doc). Applied to the Instrument or its samples.
SETTINGS = {
    "name": (_text, "the name Schism shows"),
    "nna": (_choice("cut", "cont", "off", "fade",
                    aliases={"continue": "cont"}),
            "what a new note does to the one still sounding: cut it, let "
            "it continue, key it off, or fade it"),
    "dct": (_choice("off", "note", "sample", "inst"),
            "duplicate check: look for a note/sample/instrument already "
            "sounding"),
    "dca": (_choice("cut", "off", "fade"), "what to do to a duplicate"),
    "fade": (_int(0, 256), "fadeout speed after note-off; 0 = none"),
    "pps": (_int(-32, 32), "pitch-pan separation"),
    "ppc": (_note, "pitch-pan centre note"),
    "gain": (_int(0, 128), "instrument global volume; the player applies it "
             "in steps of 2, so 0 and 1 are both silent"),
    "pan": (_int(0, 64), "default pan 0..64 (omit for the channel's)"),
    "rv": (_int(0, 100), "random volume variation, percent"),
    "rp": (_int(0, 64), "random pan variation"),
    "cutoff": (_int(0, 127), "filter cutoff 0..127 (omit: filter off)"),
    "res": (_int(0, 127), "filter resonance 0..127"),
    "vol": (_int(0, 64), "default volume of its samples"),
    "vib": (None, "sample auto-vibrato speed/depth/rate[/sine|ramp|square|"
             "random]; rate is how fast it fades in, and 0 never starts it"),
    "oct": (_octaves, "octaves a multisample covers, default 1-7"),
    "wave": (_choice(*WAVES), "what the sound is made of"),
    "sample": (_text, "use a sample defined by `smp`"),
    "kit": (None, "key:sample pairs, one sample per key, all at natural "
            "pitch; a sample written @name is a forged one"),
    "forge": (None, "a sound the forge makes: an archetype, or an "
              "instrument of a bank (`python3 -m schism forge archetypes`); "
              "layer:A@click+B@body stacks sounds by role, one a part"),
    "dna": (None, "with forge=, dials to move: axis:value,... "
            "(brightness:0.8,decay:0.3)"),
    "reg": (None, "with forge=, the register it is built for, A3; for a "
            "drum the pitch it is tuned to"),
    "chord": (_chord, "with forge=, the sample is a chord: a name (maj min "
              "7 maj7 min7 min9 sus4 ...) or the semitones above the key, "
              "0,3,7,10,14. Pressing a key plays the chord on it"),
    "root": (_int(-24, 24), "with chord=, semitones from the key to the "
             "chord's root: chord=min9 root=-3 plays A minor 9 on C"),
    "stereo": (_float(0.5, 40), "with forge=, a stereo sample: the sound "
               "twice, this many cents flat on the left and sharp on the "
               "right (a loop moves in whole steps of its pitch, so it gets "
               "the nearest it can hold)"),
    "rev": (_flag, "with forge=, play the sample backwards (a reverse "
            "cymbal); a looped sound is played out for a second and a half "
            "first, and the result does not loop"),
}
for _key in ENVELOPES:
    SETTINGS[_key] = (None, ENVELOPES[_key][2] + " envelope, tick:value,...")

_NNA = {"cut": 0, "cont": 1, "off": 2, "fade": 3}
_DCT = {"off": 0, "note": 1, "sample": 2, "inst": 3}
_DCA = {"cut": 0, "off": 1, "fade": 2}
_VIB_WAVE = {"sine": 0, "ramp": 1, "square": 2, "random": 3}


def split_options(tokens):
    """['a=1', 'b=2'] -> {'a': '1', 'b': '2'}; a bare word is an error."""
    options = {}
    for token in tokens:
        if "=" not in token:
            raise RecipeError(
                f"{token!r} is not key=value",
                "every setting is written key=value with no spaces, "
                "e.g. nna=cut")
        key, value = token.split("=", 1)
        key = key.lower()
        if key in options:
            raise RecipeError(f"{key} is given twice")
        options[key] = value
    return options


def _recipe_params(kind: str, options: dict):
    """The wave's own parameters, parsed, with defaults filled in. Consumes
    them from `options`."""
    _, params = WAVES[kind]
    values = {}
    for name, (parser, default) in params.items():
        if name in options:
            try:
                values[name] = parser(options.pop(name))
            except RecipeError as error:
                raise RecipeError(f"{name}: {error}") from None
        else:
            values[name] = default
    return values


# -- making the sound ---------------------------------------------------------
def make_sample(kind: str, params: dict):
    """-> list of (low, high, Sample) covering the notes; one entry for a
    one-shot or a pad (low=1, high=120)."""
    S = M.Sample
    full = (M.MIN_NOTE, M.MAX_NOTE)
    if kind in _MULTISAMPLED:
        def make_cycle(n, max_h):
            if kind == "fm":
                return synth.fm_cycle(n, params["ratio"], params["index"],
                                      params["fb"], max_h)
            return synth.cycle(kind, n, max_h, params.get("duty", 0.5))
        groups = synth.multisample(make_cycle, params.get("_low", 13),
                                   params.get("_high", 96))
        out = []
        for index, (low, high, data, c5) in enumerate(groups):
            lo = M.MIN_NOTE if index == 0 else low
            hi = M.MAX_NOTE if index == len(groups) - 1 else high
            out.append((lo, hi, S(data=data, c5speed=c5, loop=(0, len(data)))))
        return out
    if kind == "pad":
        data, c5 = synth.pad(params["base"], params["voices"], params["detune"],
                             params["secs"], params["shape"], params["seed"])
        return [(*full, S(data=data, c5speed=c5, loop=(0, len(data))))]
    if kind == "noise":
        data = synth.noise_loop(params["len"], params["seed"], params["color"])
        return [(*full, S(data=data, c5speed=synth.SAMPLE_RATE,
                          loop=(0, len(data))))]
    if kind == "kick":
        data = synth.kick(params["len"], params["start"], params["end"],
                          params["sweep"], params["decay"], params["drive"])
    elif kind == "snare":
        data = synth.snare(params["len"], params["tone"], params["seed"])
    elif kind in ("hat", "openhat"):
        data = synth.hat(params["len"], params["decay"], params["seed"])
    elif kind == "clap":
        data = synth.clap(params["len"], params["seed"])
    elif kind == "tom":
        data = synth.tom(params["len"], params["hz"])
    elif kind == "rim":
        data = synth.rim()
    elif kind == "pluck":
        data, c5 = synth.pluck(params["root"], params["len"], params["damp"],
                               params["bright"], params["seed"])
        return [(*full, S(data=data, c5speed=c5))]
    elif kind == "bell":
        data, c5 = synth.bell(synth.C5_HZ, params["len"])
        return [(*full, S(data=data, c5speed=c5))]
    else:                                            # pragma: no cover
        raise RecipeError(f"no wave {kind!r}")
    return [(*full, S(data=data, c5speed=synth.SAMPLE_RATE))]


class Library:
    """The named samples (`smp`) and the cache of what has been made, so two
    instruments asking for the same saw share its samples."""

    def __init__(self):
        self.named = {}              # name -> (kind, params, volume)
        self._made = {}
        self.banks = []              # forge banks loaded by `bank`
        self.base_dir = ""           # where relative bank paths start
        self.tick = None             # seconds per tick the score will have
        self.forged = {}             # instrument number -> what the forge made
        self.recipes = {}            # instrument number -> the recipe it used

    def define_forged(self, name: str, options: dict):
        """`smp NAME forge=... [dna=...] [reg=...] [vol=...]`."""
        options = dict(options)
        params = {"spec": options.pop("forge"), "dna": options.pop("dna", ""),
                  "reg": options.pop("reg", "")}
        from .forge import notation_hook
        edits = notation_hook.take_edits(options)
        if edits:
            # a recipe's parameters are hashed to cache its samples
            params["edits"] = json.dumps(edits, sort_keys=True)
        volume = _int(0, 64)(options.pop("vol", "64"))
        if options:
            raise RecipeError(
                f"{', '.join(sorted(options))} are not settings of a forged "
                f"sample", "a forged sample takes: forge, dna, reg, vol, "
                "chord, root, stereo, rev")
        self.named[name] = ("forge", params, volume)

    def define(self, name: str, kind: str, options: dict):
        params = _recipe_params(kind, options)
        volume = _int(0, 64)(options.pop("vol", "64"))
        if options:
            raise RecipeError(
                f"{', '.join(sorted(options))} are not settings of "
                f"wave={kind}", "the settings of a wave are: "
                + (", ".join(WAVES[kind][1]) or "none") + ", vol")
        self.named[name] = (kind, params, volume)

    def make(self, kind: str, params: dict):
        key = (kind, tuple(sorted(params.items(), key=lambda kv: kv[0])))
        if key not in self._made:
            if kind == "forge":
                from .forge import notation_hook
                self._made[key] = notation_hook.forged_samples(
                    dict(params), self)
            else:
                self._made[key] = make_sample(kind, dict(params))
        return self._made[key]


def compile_instrument(number: int, options: dict, library: Library,
                       module: M.Module, warn=None):
    """Build instrument `number` into `module`; return nothing.

    Appends its samples to `module.samples` and sets
    `module.instruments[number - 1]` (growing the list, padding with blank
    instruments, since the file indexes them by position). `warn(text)`, when
    given, hears about settings that compile and then play nothing.
    """
    options = dict(options)
    chosen = [k for k in ("wave", "sample", "kit", "forge") if k in options]
    if len(chosen) != 1:
        raise RecipeError(
            f"an instrument needs exactly one of wave=, sample=, kit=, "
            f"forge=; this has {len(chosen) or 'none'}",
            "e.g. wave=saw, or sample=thump, or kit=C-2:thump,D-2:snare, "
            "or forge=bell")
    if "forge" in options:
        from .forge import notation_hook
        return notation_hook.compile_forged(number, options, library, module,
                                            warn)
    if "dna" in options or "reg" in options:
        raise RecipeError(
            "dna= and reg= are settings of forge=", "write forge=NAME "
            "dna=brightness:0.8 reg=A3")

    ins = M.Instrument()
    kind = options.pop("wave", None)
    if kind is not None:
        try:
            kind = SETTINGS["wave"][0](kind)
        except RecipeError as error:
            raise RecipeError(f"wave: {error}") from None
    sample_name = options.pop("sample", None)
    kit = options.pop("kit", None)

    def take(key, convert=None):
        if key in options:
            text = options.pop(key)
            parser = SETTINGS[key][0]
            try:
                return convert(text) if convert else parser(text)
            except RecipeError as error:
                raise RecipeError(f"{key}: {error}") from None
        return None

    take_octaves = take("oct")
    low_oct, high_oct = take_octaves if take_octaves else (1, 7)
    volume = take("vol")
    name = take("name")
    ins.name = name if name is not None else f"Instrument {number}"
    for key, attr, table in (("nna", "nna", _NNA), ("dct", "dct", _DCT),
                             ("dca", "dca", _DCA)):
        value = take(key)
        if value is not None:
            setattr(ins, attr, table[value])
    for key, attr in (("fade", "fadeout"), ("pps", "pitch_pan_separation"),
                      ("gain", "global_volume"), ("pan", "default_pan"),
                      ("rv", "random_volume"), ("rp", "random_pan"),
                      ("cutoff", "cutoff"), ("res", "resonance")):
        value = take(key)
        if value is not None:
            setattr(ins, attr, value)
    if warn and ins.global_volume < 2:
        warn(f"instrument {number:02d} has gain={ins.global_volume}: the "
             f"player applies instrument volume in steps of 2, so it plays "
             f"silent. Use gain=2 or more (quiet is 8, full is 128)")
    ppc = take("ppc")
    if ppc is not None:
        ins.pitch_pan_center = ppc - 1
    vibrato = options.pop("vib", None)
    if vibrato:
        parsed_vibrato = _parse_vibrato(vibrato)
        if warn and parsed_vibrato[1] and not parsed_vibrato[2]:
            warn(f"instrument {number:02d} has vib={vibrato}: a rate of 0 "
                 f"never fades the vibrato in, so nothing wobbles. Use a rate "
                 f"of 1-64: it reaches full depth after about 256*depth/rate "
                 f"ticks")
    if "ienv" in options and "fenv" in options:
        raise RecipeError(
            "ienv and fenv are the same envelope slot — the file has one "
            "pitch/filter envelope and a flag that says which",
            "give an instrument one of them")
    for key, slot in (("venv", "volume_envelope"), ("penv", "pan_envelope"),
                      ("ienv", "pitch_envelope"), ("fenv", "pitch_envelope")):
        if key in options:
            setattr(ins, slot, parse_envelope(options.pop(key), key))
    if ins.pitch_envelope is not None and ins.pitch_envelope.filter \
            and ins.cutoff is None:
        ins.cutoff = 127           # a filter envelope needs the filter on

    sample_ids = []

    def add(sample_or_entries, label, own_volume=None):
        """Append library samples to the module; -> [(low, high, number)].

        The volume an instrument line asks for (`vol=`) wins; failing that
        the volume the `smp` line gave its sample; failing that 64."""
        entries = []
        for low, high, sample in sample_or_entries:
            clone = M.Sample(**{**sample.__dict__})
            clone.name = label[:25]
            if volume is not None:
                clone.volume = volume
            elif own_volume is not None:
                clone.volume = own_volume
            if vibrato:
                _apply_vibrato(clone, parsed_vibrato)
            module.samples.append(clone)
            entries.append((low, high, len(module.samples)))
        return entries

    if kind is not None:
        params = _recipe_params(kind, options)
        if kind in _MULTISAMPLED:
            params["_low"] = low_oct * 12 + 1
            params["_high"] = high_oct * 12 + 12
        library.recipes[number] = {
            "wave": kind, "params": _plain(params), "octaves": [low_oct, high_oct]}
        entries = add(_library_samples(library, kind, params),
                      f"{ins.name} {kind}")
        for low, high, sample_no in entries:
            ins.use_sample(sample_no, low, high)
    elif sample_name is not None:
        if sample_name not in library.named:
            raise RecipeError(
                f"no sample named {sample_name!r}",
                "define it first with `smp NAME wave=...`; defined: "
                + (", ".join(sorted(library.named)) or "none yet"))
        k, params, smp_volume = library.named[sample_name]
        library.recipes[number] = {"sample": sample_name, "wave": k,
                                   "params": _plain(params)}
        entries = add(_library_samples(library, k, params), sample_name,
                      smp_volume)
        for low, high, sample_no in entries:
            ins.use_sample(sample_no, low, high)
    else:
        kit_recipe = {}
        library.recipes[number] = {"kit": kit_recipe}
        for pair in kit.split(","):
            try:
                key_text, smp = pair.split(":")
                key = _note(key_text)
            except (ValueError, RecipeError):
                raise RecipeError(
                    f"{pair!r} is not key:sample",
                    "write the kit as C-2:kick,D-2:snare") from None
            if smp.startswith("@"):
                k, params, smp_volume = "forge", {
                    "spec": smp[1:], "dna": "", "reg": ""}, 64
            elif smp not in library.named and smp not in WAVES:
                raise RecipeError(
                    f"kit key {key_text}: no sample or wave named {smp!r}",
                    "built-in one-shots: kick snare hat openhat clap tom "
                    "rim pluck bell; @name for a forged one; or define one "
                    "with `smp`")
            if smp.startswith("@"):
                pass
            elif smp in library.named:
                k, params, smp_volume = library.named[smp]
            else:
                k, params, smp_volume = smp, _recipe_params(smp, {}), 64
            kit_recipe[key_text] = {"sample": smp, "wave": k,
                                    "params": _plain(params)}
            entries = add(_library_samples(library, k, params), smp,
                          smp_volume)
            ins.note_map[key - 1] = (60, entries[0][2])  # every key at C-5

    if options:
        names = sorted(options)
        own = list(WAVES[kind][1]) if kind else []
        owners = {}
        for other, (_, params) in WAVES.items():
            for name in params:
                owners.setdefault(name, []).append(other)
        hints = [f"{n} belongs to wave={'/'.join(owners[n])}"
                 for n in names if n in owners and n not in own]
        general = ", ".join(sorted(k for k in SETTINGS
                                   if k not in ("wave", "sample", "kit",
                                                "forge", "dna", "reg",
                                                "chord", "root", "stereo",
                                                "rev")))
        many = len(names) > 1
        raise RecipeError(
            f"{', '.join(names)} {'are' if many else 'is'} not "
            f"{'settings' if many else 'a setting'} of this instrument",
            "; ".join(hints + [f"settings for any instrument: {general}"]
                      + ([f"wave={kind} also takes: {', '.join(own)}"] if own
                         else [f"wave={kind} has no settings of its own"]
                         if kind else [])))
    while len(module.instruments) < number:
        module.instruments.append(M.Instrument(
            name="", note_map=[(n, 0) for n in range(120)]))
    module.instruments[number - 1] = ins


def _plain(params: dict) -> dict:
    """A recipe's parameters as JSON can hold them, without the internal
    ones (the ones that start with an underscore)."""
    return {k: v for k, v in params.items() if not k.startswith("_")}


def _library_samples(library: Library, kind: str, params: dict):
    return library.make(kind, params)


def _parse_vibrato(text: str):
    """'speed/depth/rate[/wave]' -> (speed, depth, rate, wave number)."""
    parts = text.split("/")
    try:
        speed, depth, rate = (int(p) for p in parts[:3])
        wave = _VIB_WAVE[parts[3].lower()] if len(parts) > 3 else 0
    except (ValueError, KeyError):
        raise RecipeError(
            f"vib={text!r} is not speed/depth/rate[/wave]",
            "e.g. vib=24/6/20 or vib=24/6/20/ramp") from None
    return (_int(0, 64)(str(speed)), _int(0, 64)(str(depth)),
            _int(0, 64)(str(rate)), wave)


def _apply_vibrato(sample, parsed):
    (sample.vibrato_speed, sample.vibrato_depth, sample.vibrato_rate,
     sample.vibrato_wave) = parsed
