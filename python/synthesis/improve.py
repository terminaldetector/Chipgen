"""improve.py — one local change to an instrument, measured, then kept or not.

The loop a small model can drive without reading the engine:

    inspect   what the instrument does now, phase by phase: a short note, a
              held note with its release, and a phrase with other parts
    patch     an intent ("darker sustain") becomes a few candidate changes
              to the instrument's program (program.py) — the code writes
              them, the model only names the intent
    probe     every candidate plays the same three things
    compare   against the baseline on the reading the intent names, with
              guards on everything else (pitch, level, clipping, the phase
              the intent did not ask to change)
    commit    the best candidate if it beat the baseline, else nothing

It is bounded: candidates x rounds (3 x 2 to start), probes of a few
seconds, a final phrase of 8-16 s. It stops when the budget is spent, when
a round finds nothing better, when every candidate of a round repeats a
state already tried, or at the first error — an error never starts another
render. The run leaves a record: the hypothesis (what problem, which
parameter, what should happen, how it is checked), the baseline, every
candidate with its change and readings, the choice and why, the seed, the
versions, and what it cost. `rollback` puts the instrument back exactly as
it was.

What it does not do: judge whether the result sounds good. It checks that
the change did what the intent says, by measurement, and keeps everything
else where it was. Listening is the A/B files' job; the record says so.

A model takes part at two points only: it names the intent, and, with
`pick="list"`, it chooses among the finalists (`commit` takes the id). Its
tokens are the caller's to count; this module counts renders and seconds.
"""

import copy
import json
import math
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import phases
from . import program as P

RECORD_FORMAT = "chipgen-improve/1"
#: 240 ticks a second: a 60 Hz frame is exactly 4 ticks, so a program's
#: writes land on the frame and the probes measure the program, not jitter
PROBE_TPS = 240
PROBE_BPM = 120
PROBE_LPB = 4
ROW_S = 60.0 / PROBE_BPM / PROBE_LPB


class ImproveError(ValueError):
    pass


@dataclass
class Budget:
    candidates: int = 3
    rounds: int = 2
    max_renders: int = 40
    final_seconds: Tuple[float, float] = (8.0, 16.0)

    @classmethod
    def parse(cls, text: str) -> "Budget":
        try:
            c, r = (int(v) for v in text.lower().split("x"))
        except ValueError:
            raise ImproveError(f"budget is CANDIDATESxROUNDS, e.g. 3x2, not "
                               f"{text!r}") from None
        if not (1 <= c <= 6 and 1 <= r <= 4):
            raise ImproveError("budget: 1-6 candidates, 1-4 rounds")
        return cls(candidates=c, rounds=r)


# --------------------------------------------------------------------------
# Probes: the same three things for every candidate
# --------------------------------------------------------------------------
def _note_name(semitone: int) -> Tuple[str, int]:
    import events as E
    return E.NOTE_NAMES[semitone % 12], semitone // 12


def _cell(semitone: int) -> str:
    name, octave = _note_name(semitone)
    return f"{name}{'-' if len(name) == 1 else ''}{octave}"


def _semitone(note: str, octave: int) -> int:
    import events as E
    return E.NOTE_NAMES.index(note) + 12 * octave


def note_score(name: str, semitone: int, rows_on: int, rows_tail: int) -> str:
    lines = [f"bpm {PROBE_BPM}", f"lpb {PROBE_LPB}", f"ticks {PROBE_TPS}",
             f"inst fm0 {name}", "cols fm0", _cell(semitone)]
    lines += ["..."] * (rows_on - 1) + ["==="] + ["..."] * rows_tail
    return "\n".join(lines) + "\n"


@dataclass
class Phrase:
    """A short passage the instrument plays with other parts. `text` is
    tracker notation with `@PART` where the instrument's name goes; `part`
    is its column. The notes are the caller's: the loop never changes them."""
    text: str
    part: str = "fm0"
    name: str = "phrase"

    @classmethod
    def default(cls, semitone: int, role: str = "lead") -> "Phrase":
        """Two bars of the instrument against a kick, a hat and a held pad:
        touching notes (legato is audible), a repeated note, a leap, a held
        note with a release, and a gap."""
        if role == "bass":
            steps = [0, None, 0, 7, None, 5, 3, None,
                     0, 0, 12, 10, 7, None, "off", None]
        else:
            steps = [0, 2, 3, 5, 7, None, 5, 3,
                     2, None, 0, None, None, "off", None, None]
        pad = semitone + (12 if role == "bass" else -12)
        rows = []
        for i, step in enumerate(steps * 2):
            part = ("===" if step == "off" else
                    "..." if step is None else _cell(semitone + step))
            drum = "kick" if i % 8 == 0 else "hat" if i % 2 == 0 else "..."
            padcell = (_cell(pad) if i == 0 else "===" if i == 30 else "...")
            rows.append(f"{part:5s} {padcell:5s} {drum}")
        text = "\n".join([f"bpm {PROBE_BPM}", f"lpb {PROBE_LPB}",
                          f"ticks {PROBE_TPS}", "inst fm0 @PART",
                          "inst fm2 strings", "vol fm2 60", "cols fm0 fm2 dac"]
                         + rows + ["...  ...   ..."] * 4) + "\n"
        return cls(text=text, part="fm0", name=f"default {role} phrase")

    def with_part(self, name: str) -> str:
        return self.text.replace("@PART", name)


class Renderer:
    """Renders probes through the real sequencer, counts the renders and
    caches what has not changed (the background, a repeated candidate)."""

    def __init__(self):
        self.renders = 0
        self.cached = 0
        self.seconds = 0.0
        self._cache = {}

    def _compose(self, source, tps=None):
        import chipgen
        started = time.time()
        result = chipgen.compose(source, ticks_per_second=tps)
        self.seconds += time.time() - started
        self.renders += 1
        import analysis
        return list(analysis.to_mono(result.audio)), result.sample_rate, \
            result

    def note(self, name: str, key: str, semitone: int, rows_on: int,
             rows_tail: int):
        cache_key = ("note", key, semitone, rows_on, rows_tail)
        if cache_key in self._cache:
            self.cached += 1
            return self._cache[cache_key]
        mono, rate, _ = self._compose(note_score(name, semitone, rows_on,
                                                 rows_tail))
        out = (mono, rate)
        self._cache[cache_key] = out
        return out

    def phrase(self, name: str, key: str, phrase: Phrase):
        """-> (solo, background, rate, written notes) for the phrase."""
        import chipgen
        import events as E
        channel = int(phrase.part[2:])
        cache_key = ("phrase", key, phrase.text)
        if cache_key in self._cache:
            self.cached += 1
            return self._cache[cache_key]
        events, _, meta = chipgen.to_events(phrase.with_part(name))
        tps = meta.ticks_per_second if meta else PROBE_TPS
        solo = [ev for ev in events if not _sounds_elsewhere(ev, channel)]
        mono, rate, _ = self._compose(solo, tps)
        bg_key = ("background", phrase.text)
        if bg_key not in self._cache:
            plain, _, _ = chipgen.to_events(phrase.with_part("organ"))
            back = [ev for ev in plain
                    if not (isinstance(ev, (E.FMNoteOn,))
                            and ev.channel == channel)]
            self._cache[bg_key] = self._compose(back, tps)[:2]
            self._cache[("written", phrase.text)] = _written(plain, channel,
                                                             tps)
        else:
            self.cached += 1
        background, _ = self._cache[bg_key]
        written = self._cache[("written", phrase.text)]
        n = min(len(mono), len(background))
        out = (mono[:n], background[:n], rate, written)
        self._cache[cache_key] = out
        return out


def _sounds_elsewhere(ev, channel) -> bool:
    import events as E
    if isinstance(ev, E.FMNoteOn):
        return ev.channel != channel
    return isinstance(ev, (E.PSGToneOn, E.PSGNoiseOn, E.DACSample,
                           E.OPLNoteOn, E.NESNoteOn, E.NESNoiseOn,
                           E.NESSample))


def _written(events, channel, tps):
    """The notes the score writes on a channel: [(on s, off s, semitone,
    touches the next)] — read from a plain render, before any program."""
    import events as E
    now, notes, current = 0, [], None
    for ev in events:
        if isinstance(ev, E.Wait):
            now += ev.ticks
            continue
        if isinstance(ev, E.FMNoteOn) and ev.channel == channel:
            if current is not None:
                notes.append((current[0], now / tps, current[1], True))
            current = (now / tps, _semitone(ev.note, ev.octave))
        elif isinstance(ev, E.FMNoteOff) and ev.channel == channel:
            if current is not None:
                notes.append((current[0], now / tps, current[1], False))
                current = None
    if current is not None:
        notes.append((current[0], now / tps, current[1], False))
    return notes


# --------------------------------------------------------------------------
# Readings
# --------------------------------------------------------------------------
def _hz(semitone: int) -> float:
    import opn2
    name, octave = _note_name(semitone)
    return opn2.note_to_freq(name, octave)


def _joins(solo, rate, notes) -> List[dict]:
    """At every place where one written note runs into the next: how far the
    level jumps up (a new attack) or dips (a key-off and a re-key), dB."""
    clean = phases.centred(solo, rate)
    env = phases.envelope(clean, rate)
    out = []
    for on, off, semis, touches in notes:
        if not touches:
            continue
        t = int(round(off * 1000.0))
        before = env[max(0, t - 25):max(1, t - 3)]
        after = env[t:t + 40]
        if not before or not after:
            continue
        level = sum(before) / len(before)
        if level <= 1e-6:
            continue
        jump = 20.0 * math.log10(max(after) / level)
        dip = 20.0 * math.log10(max(min(env[max(0, t - 2):t + 12]), 1e-9)
                                / level)
        out.append({"at_ms": round(off * 1000.0, 1),
                    "jump_db": round(jump, 2), "dip_db": round(dip, 2)})
    return out


def _note_pitches(solo, rate, notes) -> List[Optional[float]]:
    """Cents off the written pitch in the middle of each note."""
    out = []
    for on, off, semis, _ in notes:
        mid0, mid1 = on + 0.4 * (off - on), on + 0.9 * (off - on)
        seg = solo[int(mid0 * rate):int(mid1 * rate)]
        cents, _ = phases._pitch(seg, rate, _hz(semis)) if len(seg) > 64 \
            else (None, None)
        out.append(None if cents is None else round(cents, 1))
    return out


def _motion(mono, rate, f0, start, stop, hop=0.02, width=0.06):
    """Pitch deviation (cents, standard deviation) between two times."""
    values = []
    t = start
    while t + width <= stop:
        seg = mono[int(t * rate):int((t + width) * rate)]
        cents, _ = phases._pitch(seg, rate, f0)
        if cents is not None:
            values.append(cents)
        t += hop
    if len(values) < 3:
        return None
    mean = sum(values) / len(values)
    return round(math.sqrt(sum((v - mean) ** 2 for v in values)
                           / len(values)), 2)


def read(renderer: Renderer, name: str, key: str, semitone: int,
         phrase: Phrase) -> dict:
    """The three probes of one instrument, read."""
    f0 = _hz(semitone)
    mono, rate = renderer.note(name, key, semitone, 1, 4)
    short = phases.note(mono, rate, f0, ROW_S)
    mono, rate = renderer.note(name, key, semitone, 10, 5)
    held = phases.note(mono, rate, f0, 10 * ROW_S)
    clean = phases.centred(mono, rate)
    held["motion"] = {
        "early_cents": _motion(clean, rate, f0, 0.05, 0.3),
        "late_cents": _motion(clean, rate, f0, 0.55, 10 * ROW_S - 0.02)}
    solo, background, rate, notes = renderer.phrase(name, key, phrase)
    context = phases.in_context(solo, background, rate,
                                [(on, off) for on, off, _, _ in notes])
    joins = _joins(solo, rate, notes)
    pitches = _note_pitches(solo, rate, notes)
    known = [abs(c) for c in pitches if c is not None]
    #: a note "lands on its pitch" within 40 cents: less than half a
    #: semitone, so a wrong note or a glide that never arrives fails, and a
    #: vibrato of up to ~35 cents read in a 60 ms window does not
    worst = []
    for j in joins:
        worst.append(max(j["jump_db"], -j["dip_db"]))
    return {"short": short, "held": held,
            "phrase": {"context": {k: v for k, v in context.items()
                                   if k != "notes"},
                       "joins": joins,
                       "join_db": (round(sorted(worst)[len(worst) // 2], 2)
                                   if worst else None),
                       "pitches": pitches,
                       "pitch_ok": (round(sum(1 for c in known if c <= 40)
                                          / len(known), 3)
                                    if known else None)}}


def summary(readings: dict) -> dict:
    """The few numbers a model needs to compare candidates."""
    get = phases.value
    return {
        "attack_rise_ms": get(readings["short"], "attack.rise_ms"),
        "attack_brightness": get(readings["held"],
                                 "phases.attack.brightness"),
        "body_brightness": get(readings["held"], "phases.body.brightness"),
        "held_brightness": get(readings["held"], "phases.held.brightness"),
        "body_level_db": get(readings["held"], "phases.body.level_db"),
        "release_ms": get(readings["held"], "release.ms_to_minus20"),
        "vibrato_early_cents": get(readings["held"], "motion.early_cents"),
        "vibrato_late_cents": get(readings["held"], "motion.late_cents"),
        "vibrato_shape_cents": _shape(readings),
        "join_db": readings["phrase"]["join_db"],
        "pitch_ok": readings["phrase"]["pitch_ok"],
        "attack_margin_db": readings["phrase"]["context"]["attack_margin_db"],
        "mix_peak": readings["phrase"]["context"]["mix_peak"],
    }


def _shape(readings):
    """Pitch movement late in a held note less the movement early in it:
    a vibrato that waits for the note to settle reads high."""
    early = phases.value(readings["held"], "motion.early_cents")
    late = phases.value(readings["held"], "motion.late_cents")
    if early is None or late is None:
        return None
    return round(late - early, 2)


# --------------------------------------------------------------------------
# Intents
# --------------------------------------------------------------------------
@dataclass
class Intent:
    name: str
    problem: str
    parameter: str
    expected: str
    metric: str                   # a summary() key
    direction: int                # -1: lower is better, +1: higher
    relative: bool                # gain as a share of the baseline
    min_gain: float
    propose: Callable             # (program, compiled, base summary, amount) -> Program
    amounts: Tuple[float, ...]    # round 1
    guards: Tuple[str, ...] = ()  # names in GUARDS
    bounds: Tuple[float, float] = (0.0, 1e9)   # where an amount may go


def _merge_track(program, target, points, priority="program"):
    out = program.copy()
    out.tracks = [t for t in out.tracks if t.target != target]
    out.tracks.append(P.Track(target, points, "ms", "linear", None, None,
                              priority))
    return out


def _attack_end_ms(base) -> float:
    peak = base.get("attack_peak_ms") or 10.0
    return max(20.0, min(120.0, peak + 10.0))


def _darker(program, compiled, base, k):
    a = _attack_end_ms(base)
    k = int(round(k))
    return _merge_track(program, "mod.tl", [(0, 0), (a, 0), (a + 80, k)])


def _brighter_attack(program, compiled, base, k):
    a = _attack_end_ms(base)
    k = int(round(k))
    return _merge_track(program, "mod.tl", [(0, -k), (a, -k), (a + 40, 0)])


def _release(sign):
    def propose(program, compiled, base, steps):
        out = program.copy()
        roles = P.fm_roles(compiled.patch)
        current = max(P._fm_operator(compiled.patch, op).release_rate
                      for op in (1, 2, 3, 4) if roles[op] == "carrier")
        current = out.settings.get("car.rr", current)
        out.settings["car.rr"] = int(max(0, min(15, current
                                                + sign * int(round(steps)))))
        return out
    return propose


def _legato(program, compiled, base, glide):
    out = program.copy()
    out.articulation = P.Articulation("legato", float(round(glide)),
                                      out.articulation.gate)
    return out


def _later_vibrato(program, compiled, base, delay):
    out = program.copy()
    vib = dict(out.vibrato or {"depth_cents": 25.0, "speed_hz": 5.5})
    vib["delay_ms"] = float(round(delay))
    out.vibrato = vib
    return out


INTENTS: Dict[str, Intent] = {
    "darker sustain": Intent(
        "darker sustain",
        "the body of the note is as bright as its attack",
        "mod.tl (modulator level) after the attack",
        "body brightness falls; the attack keeps its edge; pitch and level "
        "stay", "body_brightness", -1, True, 0.06, _darker, (4, 8, 12),
        ("pitch", "level", "attack_kept", "clip"), (1, 30)),
    "brighter attack": Intent(
        "brighter attack",
        "the attack does not stand out from the body",
        "mod.tl (modulator level) during the attack",
        "attack brightness rises; the body does not get brighter",
        "attack_brightness", +1, True, 0.06, _brighter_attack, (4, 8, 12),
        ("pitch", "body_kept", "clip"), (1, 30)),
    "shorter release": Intent(
        "shorter release",
        "the note rings on after the key comes up",
        "car.rr (carrier release rate)",
        "the time to fall 20 dB after the key-up shrinks; nothing before "
        "the key-up changes", "release_ms", -1, True, 0.1, _release(+1),
        (1, 2, 3), ("pitch", "level", "body_kept", "clip"), (1, 6)),
    "longer release": Intent(
        "longer release",
        "the note stops dead at the key-up",
        "car.rr (carrier release rate)",
        "the time to fall 20 dB after the key-up grows",
        "release_ms", +1, True, 0.1, _release(-1), (1, 2, 3),
        ("pitch", "level", "body_kept", "clip"), (1, 6)),
    "legato": Intent(
        "legato",
        "every note of a run re-attacks, so a line sounds like a row of "
        "separate notes",
        "articulation.mode legato and glide_ms",
        "at the joins of touching notes the level neither jumps nor dips; "
        "every note still lands on its pitch",
        "join_db", -1, False, 1.0, _legato, (0, 30, 60),
        ("phrase_pitch", "clip"), (0, 200)),
    "later vibrato": Intent(
        "later vibrato",
        "the vibrato starts with the note (or there is none)",
        "vibrato.delay_ms",
        "the first 300 ms stay as steady as before; the pitch moves later "
        "in a held note",
        "vibrato_shape_cents", +1, False, 3.0, _later_vibrato,
        (250, 350, 450), ("pitch", "early_quiet", "vibrato_late", "clip"),
        (150, 1000)),
}


def _guard(name, base, cand) -> Tuple[bool, str]:
    def both(key):
        return base.get(key), cand.get(key)
    if name == "pitch":
        return True, "pitch read on the phrase"
    if name == "level":
        b, c = both("body_level_db")
        if b is None or c is None:
            return True, "body level not readable"
        return abs(c - b) <= 3.0, f"body level {b:.1f} -> {c:.1f} dB"
    if name == "attack_kept":
        b, c = both("attack_brightness")
        if b is None or c is None:
            return True, "attack brightness not readable"
        return c >= 0.85 * b, f"attack brightness {b:.2f} -> {c:.2f}"
    if name == "body_kept":
        b, c = both("body_brightness")
        if b is None or c is None:
            return True, "body brightness not readable"
        return c <= 1.15 * b and c >= 0.8 * b, \
            f"body brightness {b:.2f} -> {c:.2f}"
    if name == "phrase_pitch":
        b, c = both("pitch_ok")
        if c is None:
            return True, "pitches not readable"
        return c >= (b or 0.0) - 0.1, f"notes on pitch {b} -> {c}"
    if name == "vibrato_late":
        b, c = both("vibrato_late_cents")
        need = (b or 0.0) + 4.0
        return (c is not None and c >= need), \
            f"late pitch movement {b} -> {c} cents (needs {need:.1f})"
    if name == "early_quiet":
        b, c = both("vibrato_early_cents")
        if b is None or c is None:
            return True, "early movement not readable"
        return c <= b + 3.0, f"early pitch movement {b} -> {c} cents"
    if name == "clip":
        c = cand.get("mix_peak")
        return (c is None or c < 0.999), f"phrase peak {c}"
    raise ImproveError(f"unknown guard {name}")


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------
def versions() -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    commit = None
    try:
        commit = subprocess.run(
            ["git", "-C", here, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    import core_loader
    return {"chipgen_commit": commit, "python": platform.python_version(),
            "ym2612_core": core_loader.backend_for("libopn2"),
            "program_format": P.PROGRAM_FORMAT}


def _signature(program) -> str:
    return json.dumps(program.to_dict(), sort_keys=True)


def _gain(intent, base, cand) -> Optional[float]:
    b, c = base.get(intent.metric), cand.get(intent.metric)
    if b is None or c is None:
        return None
    if intent.relative:
        if abs(b) < 1e-9:
            return None
        return intent.direction * (c - b) / abs(b)
    return intent.direction * (c - b)


def improve(entry, intent_name: str, budget: Budget = None,
            phrase: Phrase = None, seed: int = 0, pick: str = "auto",
            log: Callable = None) -> dict:
    """Run the loop on one bank entry (YM2612). -> the record (a dict)."""
    log = log or (lambda text: None)
    budget = budget or Budget()
    if intent_name not in INTENTS:
        raise ImproveError(f"no intent {intent_name!r}; have: "
                           f"{', '.join(sorted(INTENTS))}")
    if entry.backend != "ym2612":
        raise ImproveError(f"{entry.name} is a {entry.backend} instrument; "
                           f"the intents are written for the YM2612 so far "
                           f"(an NES program can be set by hand: "
                           f"`forge.py program set`)")
    intent = INTENTS[intent_name]
    from .backends import get_backend
    backend = get_backend("ym2612")
    register = entry.genome.register or backend.default_register
    semitone = _semitone(register[0], register[1])
    role = entry.role or "lead"
    phrase = phrase or Phrase.default(semitone, "bass" if role == "bass"
                                      else "lead")
    renderer = Renderer()
    started = time.time()
    base_program = P.of(entry.compiled) or P.Program(engine="ym2612")
    record = {
        "format": RECORD_FORMAT, "instrument": entry.name, "intent": intent.name,
        "hypothesis": {"problem": intent.problem, "parameter": intent.parameter,
                       "expected": intent.expected,
                       "checked_by": f"{intent.metric} "
                                     f"({'lower' if intent.direction < 0 else 'higher'}"
                                     f" is better), guards: "
                                     f"{', '.join(intent.guards)}"},
        "budget": {"candidates": budget.candidates, "rounds": budget.rounds,
                   "max_renders": budget.max_renders},
        "seed": seed, "versions": versions(),
        "register": f"{register[0]}{register[1]}", "phrase": phrase.name,
        "before_entry": entry.to_dict(), "rounds": [], "listening":
            "not done by this loop: the readings say whether the change did "
            "what the intent says; whether it sounds better is for the A/B "
            "files"}
    seen = {_signature(base_program)}
    counter = [0]

    def install(program, tag):
        name = f"{entry.name}__{tag}"
        compiled = P.attach(copy.copy(entry.compiled), program)
        compiled.name = name
        compiled.patch = compiled.patch.copy()
        backend.install(P.prepare(compiled), entry.character)
        return name, compiled

    try:
        name, _ = install(base_program, "base")
        base_readings = read(renderer, name, "base", semitone, phrase)
    except Exception as error:      # noqa: BLE001 - recorded, not retried
        record["stop"] = {"reason": "error",
                          "detail": f"baseline: {type(error).__name__}: "
                                    f"{error}"}
        record["costs"] = _costs(renderer, started)
        return record
    base = summary(base_readings)
    base["attack_peak_ms"] = phases.value(base_readings["held"],
                                          "attack.peak_ms")
    record["baseline"] = {"program": base_program.to_dict(), "summary": base}
    log(f"baseline {intent.metric} = {base.get(intent.metric)}")
    best = {"id": "baseline", "program": base_program, "summary": base,
            "gain": 0.0, "amount": None}
    amounts = list(intent.amounts)[:budget.candidates]
    stop = None
    for round_no in range(1, budget.rounds + 1):
        if round_no > 1:
            if best["amount"] is None:
                stop = "no improvement"
                break
            a = best["amount"]
            step = max(abs(a) * 0.35, 1.0)
            lo, hi = intent.bounds
            amounts = []
            for v in (a - step, a + step, a + 2 * step):
                v = float(round(max(lo, min(hi, v))))
                if v not in amounts and v != a:
                    amounts.append(v)
            amounts = amounts[:budget.candidates]
        entries = []
        fresh = 0
        for amount in amounts:
            if renderer.renders + 3 > budget.max_renders:
                stop = "budget"
                break
            counter[0] += 1
            cid = f"r{round_no}c{counter[0]}"
            try:
                # every amount is a whole change from the baseline, so
                # round two searches around the best amount, not on top
                # of the best program
                program = intent.propose(base_program, entry.compiled, base,
                                         amount)
            except (P.ProgramError, ValueError) as error:
                entries.append({"id": cid, "amount": amount,
                                "error": str(error)})
                stop = "error"
                break
            signature = _signature(program)
            if signature in seen:
                entries.append({"id": cid, "amount": amount,
                                "repeat": True})
                continue
            seen.add(signature)
            fresh += 1
            try:
                name, _ = install(program, cid)
                readings = read(renderer, name, cid, semitone, phrase)
            except Exception as error:      # noqa: BLE001
                entries.append({"id": cid, "amount": amount,
                                "program": program.to_dict(),
                                "error": f"{type(error).__name__}: {error}"})
                stop = "error"
                break
            cand = summary(readings)
            gain = _gain(intent, base, cand)
            guards = {g: _guard(g, base, cand) for g in intent.guards}
            ok = all(passed for passed, _ in guards.values())
            if "phrase_pitch" not in intent.guards and "pitch" in \
                    intent.guards:
                b, c = base.get("pitch_ok"), cand.get("pitch_ok")
                if c is not None and b is not None and c < b - 0.1:
                    ok = False
                    guards["pitch"] = (False, f"notes on pitch {b} -> {c}")
            accepted = ok and gain is not None and gain >= intent.min_gain
            row = {"id": cid, "amount": amount, "program": program.to_dict(),
                   "delta": _delta(base_program, program), "summary": cand,
                   "gain": None if gain is None else round(gain, 4),
                   "guards": {g: {"ok": p, "why": w}
                              for g, (p, w) in guards.items()},
                   "accepted": accepted}
            entries.append(row)
            log(f"{cid} {row['delta']}: {intent.metric} "
                f"{cand.get(intent.metric)} gain {row['gain']} "
                f"{'ok' if accepted else 'rejected'}")
            if accepted and gain > best["gain"]:
                best = {"id": cid, "program": program, "summary": cand,
                        "gain": gain, "amount": amount}
        record["rounds"].append({"round": round_no, "candidates": entries})
        if stop:
            break
        if fresh == 0:
            stop = "repeated state"
            break
        improved = any(e.get("accepted") and e["id"] == best["id"]
                       for e in entries)
        if not improved:
            stop = "no improvement" if round_no > 1 or best["id"] == \
                "baseline" else stop
            if stop:
                break
    record["stop"] = {"reason": stop or "budget"}
    finalists = sorted(
        (e for r in record["rounds"] for e in r["candidates"]
         if e.get("accepted")), key=lambda e: -(e["gain"] or 0.0))
    record["finalists"] = [{"id": e["id"], "delta": e["delta"],
                            "gain": e["gain"],
                            intent.metric: e["summary"].get(intent.metric)}
                           for e in finalists]
    if pick == "list":
        record["choice"] = {"by": "pending", "why": "finalists listed for a "
                            "model or a person to choose (`commit` takes the "
                            "id)"}
        record["best"] = None
    else:
        record["choice"] = {
            "by": "auto",
            "why": ("the largest gain on the intent's reading among the "
                    "candidates that passed every guard"
                    if best["id"] != "baseline" else
                    "no candidate beat the baseline and passed the guards; "
                    "nothing changes")}
        record["best"] = {"id": best["id"],
                          "program": best["program"].to_dict(),
                          "gain": round(best["gain"], 4)}
    record["costs"] = _costs(renderer, started)
    return record


def _costs(renderer, started) -> dict:
    return {"renders": renderer.renders, "cached": renderer.cached,
            "render_seconds": round(renderer.seconds, 2),
            "wall_seconds": round(time.time() - started, 2),
            "model_calls": 0, "model_tokens": None,
            "note": "no model took part in this run; a caller that lets a "
                    "model choose counts its tokens itself"}


def _delta(before, after) -> str:
    """A short text of what changed between two programs."""
    a, b = before.to_dict(), after.to_dict()
    parts = []
    tracks_a = {t["target"]: t for t in a["tracks"]}
    for t in b["tracks"]:
        if tracks_a.get(t["target"]) != t:
            parts.append(f"{t['target']} "
                         + " ".join(f"{p[0]:g}ms:{p[1]:+g}" for p in t["points"]))
    for key, value in b["settings"].items():
        if a["settings"].get(key) != value:
            parts.append(f"{key}={value}")
    if a["vibrato"] != b["vibrato"] and b["vibrato"]:
        v = b["vibrato"]
        parts.append(f"vibrato {v['depth_cents']:g}c {v['speed_hz']:g}Hz "
                     f"after {v['delay_ms']:g}ms")
    if a["articulation"] != b["articulation"]:
        art = b["articulation"]
        parts.append(f"{art['mode']}"
                     + (f" glide {art['glide_ms']:g}ms" if art["glide_ms"]
                        else "")
                     + (f" gate {art['gate']:g}" if art["gate"] < 1 else ""))
    return "; ".join(parts) or "no change"


def chosen_program(record: dict, pick: str = None) -> Optional[P.Program]:
    """The program a record's choice (or `pick`, a candidate id) names."""
    if pick in (None, "auto"):
        best = record.get("best")
        if not best or best["id"] == "baseline":
            return None
        return P.parse(best["program"])
    for r in record.get("rounds", []):
        for e in r["candidates"]:
            if e["id"] == pick:
                if not e.get("accepted"):
                    raise ImproveError(f"{pick} did not pass its guards; "
                                       f"finalists: "
                                       + ", ".join(f["id"] for f in
                                                   record.get("finalists",
                                                              []))
                                       or "none")
                return P.parse(e["program"])
    raise ImproveError(f"no candidate {pick!r} in the record")


def commit(bank, record: dict, pick: str = None):
    """Put the chosen program on the instrument in `bank`. -> the entry, or
    None when the record chose nothing."""
    program = chosen_program(record, pick)
    if program is None:
        return None
    entry = bank.get(record["instrument"])
    entry.compiled = P.attach(entry.compiled, program)
    return entry


def rollback(bank, record: dict):
    """Put the instrument back exactly as the record found it."""
    from .bank import Entry
    before = Entry.from_dict(record["before_entry"])
    for i, e in enumerate(bank.entries):
        if e.name == before.name:
            bank.entries[i] = before
            return before
    raise ImproveError(f"{before.name} is not in the bank any more")
