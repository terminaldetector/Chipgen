"""program.py — what an instrument does over the life of a note, in a few lines.

A forge instrument is a patch: eleven dials compiled once into registers, the
same at the first millisecond of a note as at the last. A part played on real
hardware is not like that. A Mega Drive driver rewrites a bass's modulator
level while the note sounds, so the attack is bright and the body dark; it
keys a lead once and slides the pitch for the next note, so a phrase is one
breath and not a row of attacks. This module is that layer, small enough
for a small model to write and read.

Three levels, each with its own job:

    intent    words a model can choose: "darker sustain", "denser attack",
              "shorter release", "legato" (improve.py turns one into a
              program change and measures whether it worked)
    program   a compact description of the note (this module): tracks,
              settings, a vibrato, an articulation
    native    what the chip is told: FMOperator, FMAlgorithm, Portamento,
              Vibrato, key-on and key-off at given ticks (`explain` lists
              every write, so a surprising sound can be traced to one)

## A program

    {"format": "chipgen-program/1", "engine": "ym2612",
     "tracks": [{"target": "mod.tl", "points": [[0, -6], [40, 0], [300, 10]],
                 "clock": "ms", "interp": "linear", "release": null,
                 "loop": null, "priority": "program"}],
     "settings": {"car.rr": 9},
     "vibrato": {"depth_cents": 25, "speed_hz": 5.5, "delay_ms": 250},
     "articulation": {"mode": "legato", "glide_ms": 40, "gate": 1.0}}

* A **track** is one target over time from the key-on: points of (time,
  value), `step` or `linear` between them. `loop` names the point a held
  note returns to; as in an IT envelope, the last held point is where the
  loop ends (the value jumps back when the time reaches it), so a loop that
  should rest on its last value repeats that point. `release` names the
  point the note jumps to at key-off; the points from there on are timed
  from the key-off. No release point: the track runs on through the
  release as if held.
* **settings** change the patch itself (envelope rates, feedback): fixed for
  the instrument, applied when the bank is installed.
* **vibrato** is the driver's (60 Hz, per voice), with a delay from each
  key-on, so it can start after the attack.
* **articulation**: `retrigger` keys every note; `legato` keys the first
  note of a run of notes that touch and moves the pitch for the rest (no new
  attack, the envelope carries on); `glide_ms` makes that move a slide;
  `gate` lets the key up early, as a share of each written length.

## What each engine can do

The targets are the engine's own, not one list for every chip: the YM2612
has modulator levels and feedback, the NES has 4-bit volume and duty
cycles. `targets(engine)` lists them with units, range, clock, scope, what
resets them and what they conflict with; `capabilities()` is the table the
docs print. A target an engine does not have is an error naming the ones it
has — never a silent no-op.

## Honest realisation

Times are moved to the 60 Hz frame grid the driver runs on (and to the
sequencer's ticks), values to whole register steps, out-of-range values are
clamped. `explain` returns the trajectory the chip actually receives, next
to the one asked for, and every such loss by name. Nothing is claimed finer
than the clock: two writes that land in one tick are merged and that is
reported.

One hardware fact shapes all of it: a key-on does not reload the patch. A
modulator level written mid-note stays until it is written again (measured:
a bass's centroid went 1938 Hz -> 1225 Hz with the modulators turned down
mid-note and stayed at 1129 Hz on the next note). So a program writes its
starting values at every keyed note-on, and that is what `reset` means here.
"""

import copy
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

PROGRAM_FORMAT = "chipgen-program/1"
#: the driver's frame, which is also chipgen's effect clock (effects.py)
FRAME_RATE = 60.0
#: a Portamento fast enough to be a jump (nes_driver.JUMP_RATE)
JUMP_RATE = 48000.0
#: the ordinary operator number of each slot of FMInstrument.operators
LIST_TO_OP = (1, 3, 2, 4)
CLOCKS = ("ms", "frame")
INTERPS = ("linear", "step")
PRIORITIES = ("program", "pattern")
MODES = ("retrigger", "legato")


class ProgramError(ValueError):
    """A program that cannot be used, with the field named."""


# --------------------------------------------------------------------------
# What each engine can be told
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Target:
    name: str
    units: str
    low: float
    high: float
    kind: str           # "offset" from the patch, or "absolute"
    native: str         # what the chip is told
    scope: str          # note | channel | global
    sounding: str       # whether a change reaches a voice already sounding
                        # (natively), or needs the next attack
    reset: str          # what restores it
    conflicts: str      # what in a score writes the same thing

    def to_dict(self) -> dict:
        return dict(self.__dict__)


_TL = ("Total Level steps of {who} (0.75 dB each); + is quieter, which on a "
       "modulator is darker, - is brighter")
_TL_RESET = ("written again at every keyed note-on (the chip keeps the last "
             "value through a key-on); an instrument change reloads the patch")
_YM_TARGETS = {
    "mod.tl": Target(
        "mod.tl", _TL.format(who="every modulator"), -127, 127, "offset",
        "FMOperator tl on each modulator of the patch's algorithm", "note",
        "yes, at the write", _TL_RESET,
        "a score `op fmN M tl` on a modulator inside the note: `priority` "
        "says who wins"),
    "fb": Target(
        "fb", "operator 1 feedback, 0-7", 0, 7, "absolute",
        "FMAlgorithm feedback (register 0xB0)", "channel",
        "yes, at the write",
        "written again at every keyed note-on; an instrument change reloads "
        "the patch",
        "a score `alg fmN A F` inside the note: `priority` says who wins"),
    "pitch": Target(
        "pitch", "cents from the written note", -2400, 2400, "offset",
        "Portamento at jump speed on the voice (60 Hz effect clock)", "note",
        "yes, at the next 60 Hz frame",
        "back to 0 at every keyed note-on",
        "any pitch effect the score gives the channel (porta, pitch, vib, "
        "arp, 1xx/2xx/3xx/4xy/0xy): the score wins, the program's pitch and "
        "legato are left out on that channel and `explain` says so"),
}
for _n in (1, 2, 3, 4):
    _YM_TARGETS[f"op{_n}.tl"] = Target(
        f"op{_n}.tl", _TL.format(who=f"operator {_n}"), -127, 127, "offset",
        f"FMOperator tl on operator {_n} (a modulator only: a carrier's TL is "
        f"the note's level, which velocity and `vol` own)", "note",
        "yes, at the write", _TL_RESET,
        f"a score `op fmN {_n} tl` inside the note: `priority` says who wins")

_NES_TARGETS = {
    "volume": Target(
        "volume", "volume 0-15 (4 bits; the triangle is on or off)", 0, 15,
        "absolute", "the volume macro: NESVolume each frame it changes",
        "note", "yes, at the next frame", "the macro restarts at every note",
        "none: the NES driver owns the volume of a macro instrument"),
    "duty": Target(
        "duty", "pulse duty 0-3 (12.5, 25, 50, 75 %)", 0, 3, "absolute",
        "the duty macro: NESDuty each frame it changes (pulse only)", "note",
        "yes, at the next frame", "the macro restarts at every note",
        "none"),
    "pitch": Target(
        "pitch", "cents from the written note", -2400, 2400, "offset",
        "the pitch macro: a Portamento jump each frame it changes; small "
        "offsets stay inside the timer's page (no phase reset click)", "note",
        "yes, at the next frame", "the macro restarts at every note",
        "a score `porta` on the voice drives the same Portamento as the "
        "macro and the later write wins; a score vibrato adds to it"),
    "period": Target(
        "period", "noise period index 0-15 (0 is the highest pitch)", 0, 15,
        "absolute", "the period macro: NESNoiseOn with the new period each "
        "frame it changes (noise only)", "note", "yes, at the next frame",
        "the macro restarts at every note", "none"),
    "arp": Target(
        "arp", "semitones from the written note", -48, 48, "offset",
        "the arpeggio macro", "note", "yes, at the next frame",
        "the macro restarts at every note", "the score's `arp` adds to it"),
}

#: patch changes a program may make, name -> (low, high, what it is)
_YM_SETTINGS = {
    f"{who}.{field}": (0, high, f"{field} of every {name}")
    for who, name in (("car", "carrier"), ("mod", "modulator"))
    for field, high in (("ar", 31), ("d1r", 31), ("d2r", 31), ("sl", 15),
                        ("rr", 15))
}
_YM_SETTINGS["fb"] = (0, 7, "feedback of operator 1")
_OPERATOR_FIELD = {"ar": "attack_rate", "d1r": "decay_rate",
                   "d2r": "sustain_rate", "sl": "sustain_level",
                   "rr": "release_rate", "tl": "total_level"}

ENGINES = {"ym2612": (_YM_TARGETS, _YM_SETTINGS),
           "nes": (_NES_TARGETS, {})}
#: what an engine offers besides tracks, for the capability table
_ENGINE_NOTES = {
    "ym2612": [
        "articulation: retrigger | legato (one key-on per run of touching "
        "notes, the pitch moved by Portamento; glide_ms makes it a slide) | "
        "gate (key-off early, a share of the written length)",
        "vibrato: the driver's, 60 Hz, per voice, with a delay from each "
        "key-on (the chip's own LFO is one rate for all six channels, so a "
        "program does not use it)",
        "settings: envelope rates and levels of all carriers or all "
        "modulators, and feedback, fixed for the instrument",
        "not here yet: carrier levels over time (velocity and `vol` own "
        "them), algorithm changes, channel 3 special mode, SSG-EG",
    ],
    "nes": [
        "tracks become the instrument's own macros (one value a frame, loop "
        "and release points), which the NES driver already plays",
        "articulation: gate only; legato is not available (every note "
        "restarts its macros) and asking for it is an error",
        "volume is 4 bits: a slow fade is a staircase of 15 steps at most",
    ],
}


def targets(engine: str) -> Dict[str, Target]:
    try:
        return ENGINES[engine][0]
    except KeyError:
        raise ProgramError(f"no program engine {engine!r}; have: "
                           f"{', '.join(sorted(ENGINES))}") from None


def settings_of(engine: str) -> dict:
    return ENGINES[engine][1]


def capabilities(engine: str = None) -> dict:
    """{engine: {"targets": [...], "settings": {...}, "notes": [...]}},
    the contract a model reads before it writes a program."""
    out = {}
    for name in ([engine] if engine else sorted(ENGINES)):
        tracks, settings = ENGINES[name][0], ENGINES[name][1]
        out[name] = {
            "clock": f"tracks in ms or 60 Hz frames, realised on the "
                     f"{FRAME_RATE:g} Hz driver frame and the sequencer's "
                     f"ticks",
            "targets": [t.to_dict() for t in tracks.values()],
            "settings": {k: {"range": [lo, hi], "what": what}
                         for k, (lo, hi, what) in settings.items()},
            "notes": list(_ENGINE_NOTES.get(name, ())),
        }
    return out


# --------------------------------------------------------------------------
# The program itself
# --------------------------------------------------------------------------
@dataclass
class Track:
    target: str
    points: List[Tuple[float, float]]
    clock: str = "ms"
    interp: str = "linear"
    loop: Optional[int] = None
    release: Optional[int] = None
    priority: str = "program"

    def seconds(self) -> List[Tuple[float, float]]:
        scale = 0.001 if self.clock == "ms" else 1.0 / FRAME_RATE
        return [(t * scale, v) for t, v in self.points]

    def to_dict(self) -> dict:
        return {"target": self.target,
                "points": [[_num(t), _num(v)] for t, v in self.points],
                "clock": self.clock, "interp": self.interp,
                "loop": self.loop, "release": self.release,
                "priority": self.priority}


@dataclass
class Articulation:
    mode: str = "retrigger"
    glide_ms: float = 0.0
    gate: float = 1.0

    def to_dict(self) -> dict:
        return {"mode": self.mode, "glide_ms": _num(self.glide_ms),
                "gate": _num(self.gate)}


@dataclass
class Program:
    engine: str
    tracks: List[Track] = field(default_factory=list)
    settings: Dict[str, float] = field(default_factory=dict)
    vibrato: Optional[Dict[str, float]] = None
    articulation: Articulation = field(default_factory=Articulation)
    notes: List[str] = field(default_factory=list)

    def track(self, target: str) -> Optional[Track]:
        return next((t for t in self.tracks if t.target == target), None)

    def is_empty(self) -> bool:
        return not (self.tracks or self.settings or self.vibrato
                    or self.articulation.mode != "retrigger"
                    or self.articulation.gate < 1.0)

    def to_dict(self) -> dict:
        out = {"format": PROGRAM_FORMAT, "engine": self.engine,
               "tracks": [t.to_dict() for t in self.tracks],
               "settings": {k: _num(v) for k, v in self.settings.items()},
               "vibrato": (None if not self.vibrato else
                           {k: _num(v) for k, v in self.vibrato.items()}),
               "articulation": self.articulation.to_dict()}
        if self.notes:
            out["notes"] = list(self.notes)
        return out

    def copy(self) -> "Program":
        return Program.from_dict(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "Program":
        return parse(data)


def _num(x):
    x = float(x)
    return int(x) if x == int(x) else round(x, 4)


def parse(data: dict) -> Program:
    """A program from JSON, checked; every problem names its field."""
    if not isinstance(data, dict):
        raise ProgramError("a program is a JSON object")
    fmt = data.get("format", PROGRAM_FORMAT)
    if fmt != PROGRAM_FORMAT:
        raise ProgramError(f"format is {fmt!r}; this engine reads "
                           f"{PROGRAM_FORMAT!r}")
    engine = data.get("engine")
    tracks_of = targets(engine) if engine else None
    if tracks_of is None:
        raise ProgramError(f"engine: one of {', '.join(sorted(ENGINES))}")
    known = {"format", "engine", "tracks", "settings", "vibrato",
             "articulation", "notes"}
    extra = sorted(set(data) - known)
    if extra:
        raise ProgramError(f"unknown field {extra[0]!r}; a program has "
                           f"{', '.join(sorted(known))}")
    program = Program(engine=engine, notes=list(data.get("notes", [])))
    seen = set()
    for i, raw in enumerate(data.get("tracks") or []):
        where = f"tracks[{i}]"
        if not isinstance(raw, dict):
            raise ProgramError(f"{where} is an object with a target and "
                               f"points")
        target = raw.get("target")
        if target not in tracks_of:
            raise ProgramError(
                f"{where}.target {target!r} is not a target of the {engine}; "
                f"it has {', '.join(sorted(tracks_of))}")
        if target in seen:
            raise ProgramError(f"{where}: a second track for {target}")
        seen.add(target)
        spec = tracks_of[target]
        points = raw.get("points")
        if not isinstance(points, list) or not points:
            raise ProgramError(f"{where}.points: a list of [time, value]")
        clean = []
        for j, point in enumerate(points):
            try:
                t, v = float(point[0]), float(point[1])
            except (TypeError, ValueError, IndexError):
                raise ProgramError(f"{where}.points[{j}] is not [time, "
                                   f"value]") from None
            if t < 0 or (clean and t <= clean[-1][0]):
                raise ProgramError(f"{where}.points[{j}]: times start at 0 "
                                   f"or later and rise")
            if not spec.low <= v <= spec.high:
                raise ProgramError(f"{where}.points[{j}]: {target} runs "
                                   f"{spec.low:g}..{spec.high:g} "
                                   f"({spec.units}), not {v:g}")
            clean.append((t, v))
        clock = raw.get("clock", "ms")
        if clock not in CLOCKS:
            raise ProgramError(f"{where}.clock: {', '.join(CLOCKS)}")
        interp = raw.get("interp", "linear")
        if interp not in INTERPS:
            raise ProgramError(f"{where}.interp: {', '.join(INTERPS)}")
        priority = raw.get("priority", "program")
        if priority not in PRIORITIES:
            raise ProgramError(f"{where}.priority: {', '.join(PRIORITIES)}")
        loop, release = raw.get("loop"), raw.get("release")
        for name, index in (("loop", loop), ("release", release)):
            if index is not None and not (isinstance(index, int)
                                          and 0 <= index < len(clean)):
                raise ProgramError(f"{where}.{name} is the index of a point "
                                   f"(0-{len(clean) - 1}) or null")
        if loop is not None and release is not None and loop >= release:
            raise ProgramError(f"{where}: the loop point comes before the "
                               f"release point")
        program.tracks.append(Track(target, clean, clock, interp, loop,
                                    release, priority))
    allowed = settings_of(engine)
    for key, value in (data.get("settings") or {}).items():
        if key not in allowed:
            raise ProgramError(
                f"settings.{key} is not a setting of the {engine}"
                + (f"; it has {', '.join(sorted(allowed))}" if allowed
                   else "; it has none"))
        low, high, _ = allowed[key]
        if not (isinstance(value, (int, float)) and low <= value <= high
                and value == int(value)):
            raise ProgramError(f"settings.{key}: a whole number "
                               f"{low}..{high}")
        program.settings[key] = int(value)
    vib = data.get("vibrato")
    if vib:
        if engine != "ym2612":
            raise ProgramError("vibrato: the NES has no driver vibrato apart "
                               "from its macros; use a pitch track with a "
                               "loop")
        try:
            program.vibrato = {"depth_cents": float(vib["depth_cents"]),
                               "speed_hz": float(vib["speed_hz"]),
                               "delay_ms": float(vib.get("delay_ms", 0.0))}
        except (KeyError, TypeError, ValueError):
            raise ProgramError("vibrato: {depth_cents, speed_hz, delay_ms}") \
                from None
        if not (0 <= program.vibrato["depth_cents"] <= 200
                and 0 < program.vibrato["speed_hz"] <= 20
                and 0 <= program.vibrato["delay_ms"] <= 5000):
            raise ProgramError("vibrato: depth 0-200 cents, speed 0-20 Hz, "
                               "delay 0-5000 ms")
    art = data.get("articulation") or {}
    mode = art.get("mode", "retrigger")
    if mode not in MODES:
        raise ProgramError(f"articulation.mode: {', '.join(MODES)}")
    if mode == "legato" and engine == "nes":
        raise ProgramError(
            "articulation.mode legato is not available on the NES: every "
            "note restarts its macros (and moving the timer's high byte "
            "resets the pulse's phase); use retrigger, or the YM2612")
    gate = float(art.get("gate", 1.0))
    if not 0.1 <= gate <= 1.0:
        raise ProgramError("articulation.gate: 0.1-1.0 of the written length")
    glide = float(art.get("glide_ms", 0.0))
    if not 0.0 <= glide <= 2000.0:
        raise ProgramError("articulation.glide_ms: 0-2000")
    program.articulation = Articulation(mode, glide, gate)
    if glide and program.track("pitch") is not None:
        raise ProgramError(
            "articulation.glide_ms with a pitch track: both move the pitch "
            "with the driver's Portamento and the later one would cut the "
            "other off; keep one")
    return program


# --------------------------------------------------------------------------
# A track over the life of one note
# --------------------------------------------------------------------------
def _interp(points, t, step):
    if t <= points[0][0]:
        return points[0][1]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t < t1:
            if step:
                return v0
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return points[-1][1]


def value_at(track: Track, t: float, key_off: Optional[float]) -> float:
    """What the track asks for `t` seconds after the key-on, the key coming
    up at `key_off` seconds (None: still held)."""
    pts = track.seconds()
    step = track.interp == "step"
    held = pts[:track.release] if track.release is not None else pts
    if key_off is not None and t >= key_off and track.release is not None:
        tail = pts[track.release:]
        start = tail[0][0]
        return _interp(tail, start + (t - key_off), step)
    if track.loop is not None and t > held[-1][0]:
        t_loop, t_last = held[track.loop][0], held[-1][0]
        period = t_last - t_loop
        if period > 0:
            t = t_loop + (t - t_last) % period
        else:
            return held[-1][1]
    return _interp(held, t, step)


# --------------------------------------------------------------------------
# YM2612
# --------------------------------------------------------------------------
def fm_roles(patch) -> Dict[int, str]:
    """{ordinary operator number: "carrier" | "modulator"} for a patch."""
    carriers = {LIST_TO_OP[i] for i in patch.carrier_indices()}
    return {op: ("carrier" if op in carriers else "modulator")
            for op in (1, 2, 3, 4)}


def _fm_operator(patch, op: int):
    return patch.operators[LIST_TO_OP.index(op)]


def check_fm(program: Program, patch) -> List[str]:
    """Problems a program has with one patch (a carrier asked for as a
    modulator); [] when it fits."""
    roles = fm_roles(patch)
    problems = []
    for track in program.tracks:
        if track.target.startswith("op") and track.target.endswith(".tl"):
            op = int(track.target[2])
            if roles[op] == "carrier":
                problems.append(
                    f"{track.target}: operator {op} is a carrier in algorithm "
                    f"{patch.algorithm}, so its TL is the note's level, not "
                    f"its colour; velocity and `vol` own the level (use "
                    f"mod.tl or a modulator: "
                    f"{', '.join(str(o) for o, r in roles.items() if r == 'modulator') or 'none'})")
        if track.target == "mod.tl" and all(r == "carrier"
                                           for r in roles.values()):
            problems.append("mod.tl: algorithm 7 has no modulators; every "
                            "operator is heard, so a level change is a "
                            "level change (pick a patch with modulators)")
    return problems


def apply_settings(program: Program, patch):
    """-> (a copy of the patch with the program's settings in it, [lines
    saying what changed])."""
    out = patch.copy()
    changed = []
    roles = fm_roles(out)
    for key, value in sorted(program.settings.items()):
        if key == "fb":
            if out.feedback != value:
                changed.append(f"feedback {out.feedback} -> {value}")
            out.feedback = int(value)
            continue
        who, name = key.split(".")
        want = "carrier" if who == "car" else "modulator"
        attr = _OPERATOR_FIELD[name]
        for op in (1, 2, 3, 4):
            if roles[op] != want:
                continue
            operator = _fm_operator(out, op)
            before = getattr(operator, attr)
            if before != value:
                changed.append(f"op{op} {name} {before} -> {value}")
            setattr(operator, attr, int(value))
    return out, changed


@dataclass
class NoteCompile:
    """One note's share of a program: events at tick offsets from its key-on,
    and what the chip was told against what was asked."""
    events: List[Tuple[int, int, object]] = field(default_factory=list)
    #: native register -> [(seconds from key-on, value written)]
    writes: Dict[str, List[Tuple[float, float]]] = field(default_factory=dict)
    #: target -> [(seconds, asked, realised)] one per frame
    trajectory: Dict[str, List[Tuple[float, float, float]]] = field(
        default_factory=dict)
    losses: List[dict] = field(default_factory=list)

    def loss(self, kind: str, target: str, text: str, size: float = 0.0):
        for existing in self.losses:
            if existing["kind"] == kind and existing["target"] == target:
                existing["size"] = max(existing["size"], round(size, 3))
                return
        self.losses.append({"kind": kind, "target": target, "text": text,
                            "size": round(size, 3)})


def fm_note(program: Program, patch, channel: int, tps: float,
            key_off_ticks: Optional[int], end_ticks: int,
            pitch_base: float = 0.0, start_frame: int = 0,
            skip: Dict[str, int] = None,
            pitch_allowed: bool = True) -> NoteCompile:
    """The writes one keyed note of a YM2612 program needs.

    Ticks are offsets from the key-on. `end_ticks` is where the program stops
    looking after this note (the next keyed note, an instrument change, the
    end); `key_off_ticks` is the key-up inside that (None: held to the end).
    `pitch_base` is the legato offset in cents that a pitch track adds to.
    `start_frame` > 0 continues a note whose program has been running (a
    legato note carries on, it does not restart). `skip` names native
    registers the score owns from a tick on (priority "pattern"); where the
    program has priority, the placement pass writes its value back after
    the score's write (`value_at_tick`).
    """
    out = NoteCompile()
    roles = fm_roles(patch)
    skip = skip or {}
    end_s = end_ticks / tps
    key_off_s = None if key_off_ticks is None else key_off_ticks / tps
    frames = max(1, int(math.floor(end_s * FRAME_RATE)) + 1)
    for track in program.tracks:
        if track.target == "pitch" and not pitch_allowed:
            continue
        if track.target == "mod.tl":
            registers = [f"op{op}.tl" for op in (1, 2, 3, 4)
                         if roles[op] == "modulator"]
        elif track.target.endswith(".tl"):
            registers = [track.target]
        else:
            registers = [track.target]
        # times that are not on the frame grid move to it
        for t, _ in track.seconds():
            off = abs(t * FRAME_RATE - round(t * FRAME_RATE)) / FRAME_RATE
            if off > 1e-9:
                out.loss("clock", track.target,
                         f"{track.target}: point times moved to the "
                         f"{FRAME_RATE:g} Hz frame grid", off * 1000.0)
        last: Dict[str, float] = {}
        last_tick: Dict[str, int] = {}
        for k in range(start_frame, start_frame + frames):
            t = k / FRAME_RATE
            local = t - start_frame / FRAME_RATE
            asked = value_at(track, t, None if key_off_s is None
                             else key_off_s + start_frame / FRAME_RATE)
            tick = int(round(local * tps))
            if tick > end_ticks or (tick == end_ticks and k > start_frame):
                break
            for reg in registers:
                if reg in skip and tick >= skip[reg]:
                    continue
                if reg == "pitch":
                    value = round(pitch_base + asked, 1)
                    realised = value - pitch_base
                    event = _pitch_event(channel, value)
                elif reg == "fb":
                    value = int(round(asked))
                    realised = value
                    event = _fb_event(channel, value)
                else:
                    op = int(reg[2])
                    base = _fm_operator(patch, op).total_level
                    want = base + asked
                    value = int(round(max(0, min(127, want))))
                    if want < -0.5 or want > 127.5:
                        out.loss("clamp", track.target,
                                 f"{reg}: TL {base}{asked:+.0f} is outside "
                                 f"0-127 and was clamped", abs(
                                     want - max(0, min(127, want))))
                    realised = value - base
                    event = _tl_event(channel, op, value)
                if abs(realised - asked) > 1e-9 and reg != "pitch":
                    out.loss("steps", track.target,
                             f"{track.target}: values rounded to whole "
                             f"register steps", abs(realised - asked))
                out.trajectory.setdefault(reg, []).append(
                    (round(local, 4), round(asked, 3), round(realised, 3)))
                if k > start_frame and last.get(reg) == value:
                    continue
                if last_tick.get(reg) == tick and k > start_frame:
                    out.loss("merged", track.target,
                             f"{reg}: two writes in one sequencer tick "
                             f"({tps:g} ticks/s); the later one plays")
                jitter = abs(tick / tps - local)
                if jitter > 1e-6:
                    out.loss("clock", "frame->tick",
                             f"the {FRAME_RATE:g} Hz frames land on the "
                             f"nearest of {tps:g} sequencer ticks a second",
                             jitter * 1000.0)
                last[reg] = value
                last_tick[reg] = tick
                out.events.append((tick, 10, event))
                out.writes.setdefault(reg, []).append(
                    (round(tick / tps, 4), value))
    return out


def value_at_tick(note: NoteCompile, register: str, tick: int, tps: float):
    """The value the program had written to `register` by `tick` (offset
    from the key-on), or None if it had written nothing yet."""
    value = None
    for seconds, written in note.writes.get(register, ()):
        if seconds * tps <= tick + 1e-6:
            value = written
    return value


def native_event(channel: int, register: str, value):
    """The event that writes `value` to a program's native register."""
    if register == "pitch":
        return _pitch_event(channel, value)
    if register == "fb":
        return _fb_event(channel, value)
    return _tl_event(channel, int(register[2]), value)


def _tl_event(channel, op, value):
    import events as E
    return E.FMOperator(channel=channel, operator=op, field="tl",
                        value=int(value))


def _fb_event(channel, value):
    import events as E
    return E.FMAlgorithm(channel=channel, algorithm=None,
                         feedback=int(value))


def _pitch_event(channel, cents):
    import events as E
    return E.Portamento(target=f"fm{channel}", cents_per_second=JUMP_RATE,
                        to_cents=float(max(-4800.0, min(4800.0, cents))))


# --------------------------------------------------------------------------
# NES: tracks become the instrument's macros
# --------------------------------------------------------------------------
def _frames_of(track: Track, start: float, stop: float, key_off):
    out = []
    k = int(round(start * FRAME_RATE))
    while k / FRAME_RATE <= stop + 1e-9:
        out.append(value_at(track, k / FRAME_RATE, key_off))
        k += 1
    return out


def nes_macro(track: Track):
    """-> (nes_driver.Macro, [losses]) for one track, one value a frame."""
    from . import nes_driver
    spec = _NES_TARGETS[track.target]
    pts = track.seconds()
    losses = []
    held = pts[:track.release] if track.release is not None else pts
    held_end = held[-1][0]
    values = _frames_of(track, 0.0, held_end, None)
    loop = None
    if track.loop is not None:
        loop = int(round(held[track.loop][0] * FRAME_RATE))
        loop = min(loop, len(values) - 1)
    release = None
    if track.release is not None:
        tail = pts[track.release:]
        release = len(values)
        # the release part, timed from its own first point
        rel = Track(track.target, [(t - tail[0][0], v) for t, v in tail],
                    "ms", track.interp)
        rel.points = [(t * 1000.0, v) for t, v in rel.points]
        values += _frames_of(rel, 0.0, tail[-1][0] - tail[0][0], None)
    rounded = []
    worst = 0.0
    whole = track.target in ("volume", "duty", "arp", "period")
    for v in values:
        r = float(int(round(v))) if whole else round(v, 1)
        worst = max(worst, abs(r - v))
        rounded.append(r)
    if worst > 1e-9:
        losses.append({"kind": "steps", "target": track.target,
                       "text": f"{track.target}: values rounded to whole "
                               f"steps ({spec.units})",
                       "size": round(worst, 3)})
    for t, _ in pts:
        off = abs(t * FRAME_RATE - round(t * FRAME_RATE)) / FRAME_RATE
        if off > 1e-9:
            losses.append({"kind": "clock", "target": track.target,
                           "text": f"{track.target}: point times moved to "
                                   f"the 60 Hz frame grid",
                           "size": round(off * 1000.0, 3)})
            break
    if loop is None and release is None:
        loop = len(rounded) - 1                 # hold the last value
    if release is not None and loop is not None and loop >= release:
        loop = None
    return nes_driver.Macro(rounded, loop, release), losses


def apply_nes(program: Program, inst):
    """-> (a copy of an NESInstrument with the program's tracks as its
    macros, [losses])."""
    out = inst.copy()
    losses = []
    for track in program.tracks:
        if track.target == "duty" and out.channel != "pulse":
            raise ProgramError(f"duty: {out.name} plays the {out.channel}, "
                               f"which has no duty cycle")
        if track.target == "period" and out.channel != "noise":
            raise ProgramError(f"period: {out.name} plays the {out.channel}; "
                               f"a period index is the noise channel's")
        macro, lost = nes_macro(track)
        setattr(out, track.target, macro)
        losses += lost
    return out, losses


# --------------------------------------------------------------------------
# Programs on compiled instruments
# --------------------------------------------------------------------------
def of(compiled) -> Optional[Program]:
    """The program a compiled instrument carries, or None."""
    data = compiled.style.get("program") if compiled is not None else None
    return parse(data) if data else None


def attach(compiled, program: Optional[Program]):
    """-> a copy of `compiled` that carries `program` (None removes it)."""
    out = copy.copy(compiled)
    out.style = dict(compiled.style)
    if program is None or program.is_empty():
        out.style.pop("program", None)
    else:
        out.style["program"] = program.to_dict()
    return out


def prepare(compiled):
    """What `install` should register for an instrument with a program:
    the patch with the program's settings in it (FM), or the macros made
    from its tracks (NES). An instrument without a program is returned as
    it is."""
    program = of(compiled)
    if program is None:
        return compiled
    out = copy.copy(compiled)
    out.style = dict(compiled.style)
    if compiled.backend == "ym2612":
        problems = check_fm(program, compiled.patch)
        if problems:
            raise ProgramError(f"{compiled.name}: {problems[0]}")
        out.patch, _ = apply_settings(program, compiled.patch)
        out.patch.name = compiled.name
    elif compiled.backend == "nes":
        out.patch, _ = apply_nes(program, compiled.patch)
        out.patch.name = compiled.name
        if program.articulation.gate < 1.0:
            out.style["gate"] = program.articulation.gate
    else:
        raise ProgramError(f"{compiled.name}: programs are made for "
                           f"{', '.join(sorted(ENGINES))}, not "
                           f"{compiled.backend}")
    return out


def explain(compiled, program: Program = None, hold: float = 0.6,
            tail: float = 0.4, tps: float = 192.0, channel: int = 0) -> dict:
    """One note of an instrument, written out the way the chip receives it:
    the patch after the program's settings, every native write with its
    time, the trajectory asked for against the one realised, and every
    loss. -> a JSON-able dict."""
    program = program or of(compiled)
    if program is None:
        return {"instrument": compiled.name, "engine": compiled.backend,
                "program": None, "native": [], "losses": [],
                "notes": ["no program: the patch plays as it is"]}
    out = {"instrument": compiled.name, "engine": program.engine,
           "program": program.to_dict(), "hold_s": hold, "tail_s": tail,
           "ticks_per_second": tps}
    lines, losses, notes = [], [], []
    if program.engine == "ym2612":
        problems = check_fm(program, compiled.patch)
        if problems:
            return dict(out, native=[], losses=[], problems=problems)
        patch, changed = apply_settings(program, compiled.patch)
        notes += [f"setting: {c}" for c in changed] or \
            (["settings: none"] if not program.settings else
             ["settings: the patch already has them"])
        roles = fm_roles(patch)
        notes.append("operators: " + ", ".join(
            f"op{op} {roles[op]} TL {_fm_operator(patch, op).total_level}"
            for op in (1, 2, 3, 4)))
        hold_ticks = int(round(hold * tps))
        gated = max(1, int(round(hold_ticks * program.articulation.gate)))
        end = int(round((hold + tail) * tps))
        lines.append(f"t=0 ms key-on fm{channel}")
        note = fm_note(program, patch, channel, tps, gated, end)
        for tick, _, ev in sorted(note.events, key=lambda e: (e[0], e[1])):
            lines.append(f"t={1000.0 * tick / tps:.0f} ms {_native(ev)}")
        if program.vibrato:
            v = program.vibrato
            lines.append(f"t=0 ms vib fm{channel} {v['depth_cents']:g} "
                         f"{v['speed_hz']:g} {v['delay_ms'] / 1000.0:g} "
                         f"(the driver starts it {v['delay_ms']:g} ms after "
                         f"each key-on)")
        lines.append(f"t={1000.0 * gated / tps:.0f} ms key-off fm{channel}"
                     + (f" (gate {program.articulation.gate:g})"
                        if program.articulation.gate < 1.0 else ""))
        out["trajectory"] = {k: compact(v)
                             for k, v in note.trajectory.items()}
        out["writes"] = note.writes
        losses = note.losses
        if program.articulation.mode == "legato":
            notes.append(
                "legato: the first of a run of touching notes is keyed; the "
                "others move the pitch (Portamento"
                + (f", a {program.articulation.glide_ms:g} ms slide"
                   if program.articulation.glide_ms else ", a jump")
                + "), keep the envelope and the program running, and lose "
                  "their own velocity (no key-on, no velocity)")
    else:
        inst, losses = apply_nes(program, compiled.patch)
        for track in program.tracks:
            macro = getattr(inst, track.target)
            lines.append(f"{track.target} macro: "
                         + " ".join(f"{v:g}" for v in macro.values[:48])
                         + (" ..." if len(macro.values) > 48 else "")
                         + f"  (loop {macro.loop}, release {macro.release})")
        if program.articulation.gate < 1.0:
            notes.append(f"gate {program.articulation.gate:g}: the macros "
                         f"release that share of the way into each note")
    out["native"] = lines
    out["losses"] = losses
    out["notes"] = notes
    return out


def compact(frames):
    """[(seconds, asked, realised)] per frame -> [[ms, asked, realised]]
    only where something changes, and the last frame."""
    out = []
    for i, (t, asked, real) in enumerate(frames):
        if i == 0 or i == len(frames) - 1 or real != frames[i - 1][2] \
                or abs(asked - frames[i - 1][1]) > 1e-9 and \
                abs(asked - real) > 0.5:
            out.append([round(t * 1000.0, 1), asked, real])
    return out


def _native(ev) -> str:
    name = type(ev).__name__
    if name == "FMOperator":
        return f"op fm{ev.channel} {ev.operator} {ev.field} {ev.value}"
    if name == "FMAlgorithm":
        return f"alg fm{ev.channel} feedback {ev.feedback}"
    if name == "Portamento":
        return (f"porta {ev.target} jump to {ev.to_cents:+g} cents"
                if ev.cents_per_second >= JUMP_RATE else
                f"porta {ev.target} {ev.cents_per_second:g} c/s to "
                f"{ev.to_cents:+g} cents")
    return name
