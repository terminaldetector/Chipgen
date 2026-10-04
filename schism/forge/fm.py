"""fm.py — a four-operator FM voice with the registers of a YM2612.

The Mega Drive's sound chip has six channels of four sine operators. A
voice is a handful of small integers, in the units the chip's registers hold
them: an algorithm (how the four are wired), a feedback level on the first,
and for each operator a frequency multiple (MUL), a detune (DT), a total
level (TL, 0.75 dB a step), a key scale, and an envelope of attack, first
decay, sustain level, second decay and release rates (AR, D1R, SL, D2R, RR).
That is what a patch dump on a real console is, and it is what this module
keeps: the numbers, and a renderer that turns them into PCM so that an
Impulse Tracker instrument can host the voice.

It is an approximation of the chip, not an emulation. Operators are ideal
sines, the envelope is the register's rate turned into seconds by the rule
the chip follows (every four steps of the rate index double the speed) with
a time scale fixed by ear and by the published decay tables, the feedback
and modulation depths are the chip's (an operator at full level shifts the
phase of what it modulates by 4 pi), and there is no LFO, no SSG-EG and no
DAC quantisation of the output. The operator numbers are exact; whether a
dump sounds on a console as it does here is for a console to say, and
Chipgen's own YM2612 emulator is the place to ask.

Two ways to render:

    render_note      a note in real time, from key-on to the end of its
                     release: percussive voices, and the intro of the rest
    render_steady    the same voice held, as a loop that joins itself: every
                     operator on a whole number of cycles of the loop, the
                     envelope frozen at its sustain level, the feedback
                     settled. A detune becomes whole steps of the loop's
                     pitch (the bins of `spectral`), the way a unison does.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional

ALGORITHMS = {
    # operator index -> the operators that modulate it; the carriers sum
    0: {"mods": {1: [0], 2: [1], 3: [2]}, "carriers": [3]},
    1: {"mods": {2: [0, 1], 3: [2]}, "carriers": [3]},
    2: {"mods": {2: [1], 3: [0, 2]}, "carriers": [3]},
    3: {"mods": {1: [0], 3: [1, 2]}, "carriers": [3]},
    4: {"mods": {1: [0], 3: [2]}, "carriers": [1, 3]},
    5: {"mods": {1: [0], 2: [0], 3: [0]}, "carriers": [1, 2, 3]},
    6: {"mods": {1: [0]}, "carriers": [1, 2, 3]},
    7: {"mods": {}, "carriers": [0, 1, 2, 3]},
}
ALGORITHM_DOC = {
    0: "1>2>3>4 (a chain: the most modulated, one carrier)",
    1: "(1+2)>3>4",
    2: "(1+(2>3))>4",
    3: "((1>2)+3)>4",
    4: "(1>2)+(3>4): two pairs, two carriers",
    5: "1>(2+3+4): one modulator, three carriers",
    6: "(1>2)+3+4",
    7: "1+2+3+4: all carriers (additive; an organ)",
}
#: the phase a full-level operator shifts what it modulates by, radians
MODULATION = 4.0 * math.pi
#: feedback level -> the phase the average of the last two outputs shifts
#: operator 1 by
FEEDBACK = (0.0, math.pi / 16, math.pi / 8, math.pi / 4, math.pi / 2,
            math.pi, 2.0 * math.pi, 4.0 * math.pi)
#: one step of DT, as a share of an operator's frequency
DT_RATIO = 0.0011
#: the envelope's range, dB
FULL_DB = 96.0
TL_STEP_DB = 0.75
SL_STEP_DB = 3.0           # and SL 15 is 93 dB, as on the chip
#: seconds the envelope takes to travel FULL_DB at rate index 0 (never) .. 63
RATE_SECONDS_AT_ZERO = 40.0
PRE_ROLL_CYCLES = 60
#: the envelope of a held voice is in the sample for this long, seconds; a
#: slower fall holds at the level it had reached
HOLD_AFTER = 0.5
#: a carrier quieter than this at HOLD_AFTER is gone, and one that goes on
#: falling by more than FALL_DB over the next two seconds is a note that dies
#: away, not a voice that is held
AUDIBLE_DB = 60.0
FALL_DB = 12.0
FIELDS = (("mul", 0, 15), ("dt", -3, 3), ("tl", 0, 127), ("ks", 0, 3),
          ("ar", 0, 31), ("d1r", 0, 31), ("sl", 0, 15), ("d2r", 0, 31),
          ("rr", 0, 15))


class FmError(ValueError):
    """A dump the chip's registers cannot hold."""


@dataclass
class Operator:
    mul: int = 1        # frequency multiple 0..15 (0 is one half)
    dt: int = 0         # detune -3..3
    tl: int = 0         # total level 0..127, 0.75 dB a step (127 is silent)
    ks: int = 0         # key scale 0..3: faster envelopes on higher keys
    ar: int = 31        # attack rate 0..31 (0 never attacks)
    d1r: int = 0        # first decay rate 0..31
    sl: int = 0         # sustain level 0..15, 3 dB a step: where D1R ends
    d2r: int = 0        # second decay rate 0..31 (0 holds)
    rr: int = 7         # release rate 0..15

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k, _, _ in FIELDS}

    @classmethod
    def from_dict(cls, data: dict) -> "Operator":
        unknown = sorted(set(data) - {k for k, _, _ in FIELDS})
        if unknown:
            raise FmError(f"an operator has no {', '.join(unknown)}; it has "
                          f"{', '.join(k for k, _, _ in FIELDS)}")
        return cls(**{k: int(v) for k, v in data.items()})

    def problems(self, label: str) -> List[str]:
        out = []
        for key, low, high in FIELDS:
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) \
                    or not low <= value <= high:
                out.append(f"{label} {key.upper()} is {value!r}; the "
                           f"register takes {low}..{high}")
        return out


@dataclass
class FmPatch:
    algorithm: int = 4
    feedback: int = 0
    ops: List[Operator] = field(default_factory=lambda: [Operator()
                                                         for _ in range(4)])
    #: which sides the voice is on: the chip's panning register (L, R or LR)
    pan: str = "LR"

    def to_dict(self) -> dict:
        return {"algorithm": self.algorithm, "feedback": self.feedback,
                "pan": self.pan, "operators": [o.to_dict() for o in self.ops]}

    @classmethod
    def from_dict(cls, data: dict) -> "FmPatch":
        try:
            ops = [Operator.from_dict(o) for o in data["operators"]]
            patch = cls(int(data["algorithm"]), int(data.get("feedback", 0)),
                        ops, data.get("pan", "LR"))
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, FmError):
                raise
            raise FmError(f"an FM voice needs `algorithm` and four "
                          f"`operators` ({error})") from None
        bad = patch.problems()
        if bad:
            raise FmError("; ".join(bad[:3]))
        return patch

    def problems(self) -> List[str]:
        out = []
        if self.algorithm not in ALGORITHMS:
            out.append(f"algorithm is {self.algorithm!r}; the chip has 0..7")
        if not isinstance(self.feedback, int) or not 0 <= self.feedback <= 7:
            out.append(f"feedback is {self.feedback!r}; the register takes "
                       f"0..7")
        if len(self.ops) != 4:
            out.append(f"{len(self.ops)} operators; a voice has four")
        if self.pan not in ("L", "R", "LR"):
            out.append(f"pan is {self.pan!r}; the chip has L, R or LR")
        for n, op in enumerate(self.ops, 1):
            out.extend(op.problems(f"operator {n}"))
        return out

    def carriers(self) -> List[int]:
        return list(ALGORITHMS[self.algorithm]["carriers"])


# -- the envelope --------------------------------------------------------------------------
def key_code(note: int) -> int:
    """IT note (1 = C-0) -> the chip's 5-bit key code: the octave and which
    quarter of it the note is in."""
    octave = max(0, min(7, (note - 1) // 12))
    return min(31, 4 * octave + (0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3)[(note - 1) % 12])


def rate_index(rate: float, kc: int, ks: int, extra: float = 0.0) -> float:
    """The effective rate on the chip's 0..63 scale: twice the register, plus
    the key scale; 0 stays 0 (an envelope that never moves)."""
    if rate <= 0:
        return 0.0
    return min(63.0, 2.0 * rate + extra + (kc >> (3 - ks)))


def rate_seconds(index: float) -> float:
    """Seconds to travel the whole 96 dB at a rate index: every four steps
    double the speed."""
    if index <= 0:
        return math.inf
    return RATE_SECONDS_AT_ZERO * 2.0 ** (-index / 4.0)


class Envelope:
    """One operator's attenuation in dB at a time, for a given key and gate."""

    def __init__(self, op: Operator, note: int, gate: Optional[float]):
        kc = key_code(note)
        a = rate_seconds(rate_index(op.ar, kc, op.ks))
        self.tau = a / 5.0 if a != math.inf else math.inf
        self.t_attack = 6.17 * self.tau if self.tau != math.inf else math.inf
        self.slope1 = FULL_DB / rate_seconds(rate_index(op.d1r, kc, op.ks))
        self.slope2 = FULL_DB / rate_seconds(rate_index(op.d2r, kc, op.ks))
        self.sl_db = 93.0 if op.sl >= 15 else op.sl * SL_STEP_DB
        self.slope_r = FULL_DB / rate_seconds(rate_index(op.rr, kc, op.ks, 1))
        self.tl_db = op.tl * TL_STEP_DB
        self.gate = gate
        self.att_gate = self._held(gate) if gate is not None else 0.0

    def _held(self, t: float) -> float:
        """Attenuation with the key down."""
        if self.tau == math.inf:
            return FULL_DB
        if t < self.t_attack:
            return FULL_DB * math.exp(-t / self.tau)
        u = t - self.t_attack
        if self.slope1 > 0.0 and self.slope1 * u < self.sl_db:
            return self.slope1 * u
        if self.slope1 > 0.0:
            reach = self.sl_db / self.slope1
            return min(FULL_DB, self.sl_db + self.slope2 * (u - reach))
        return min(FULL_DB, self.slope2 * u)

    def att(self, t: float) -> float:
        if self.gate is None or t < self.gate:
            return self._held(t)
        return min(FULL_DB, self.att_gate + self.slope_r * (t - self.gate))

    def held(self, t: float) -> float:
        return self._held(t)

    def amplitude(self, n: int, rate: float, frozen_db: Optional[float] = None):
        """Linear amplitude per frame (TL included). `frozen_db` replaces the
        envelope by a constant attenuation."""
        if frozen_db is not None:
            level = 10.0 ** (-(self.tl_db + frozen_db) / 20.0)
            return [level] * n
        out = []
        att = self.att
        tl = self.tl_db
        for i in range(n):
            a = att(i / rate)
            out.append(0.0 if a >= FULL_DB - 0.01
                       else 10.0 ** (-(tl + a) / 20.0))
        return out


def steady_levels(patch: FmPatch, note: int = 49):
    """-> [attenuation in dB each operator is held at], or None if the voice
    does not hold: its carriers die away, or go on falling. An operator that
    is gone by HOLD_AFTER is held at silence (a key click is exactly that),
    and one still moving is held where it had got to."""
    carriers = patch.carriers()
    envs = [Envelope(op, note, None) for op in patch.ops]
    levels = []
    audible = False
    for k, env in enumerate(envs):
        level = env.held(HOLD_AFTER)
        gone = level >= AUDIBLE_DB
        if k in carriers and not gone:
            audible = True
            if env.held(HOLD_AFTER + 2.0) - level > FALL_DB:
                return None
        levels.append(FULL_DB if gone else level)
    return levels if audible else None


def is_steady(patch: FmPatch, note: int = 49) -> bool:
    """Does the voice hold a level while the key is down (an organ, a lead, a
    pad) rather than die away (a pluck, a bell, a stab)?"""
    return steady_levels(patch, note) is not None


def settle_time(patch: FmPatch, levels, note: int = 49) -> float:
    """Seconds until every operator's envelope is within 0.3 dB of the level
    it is held at (at most HOLD_AFTER): how long the intro must be."""
    worst = 0.0
    for op, level in zip(patch.ops, levels):
        env = Envelope(op, note, None)
        t = HOLD_AFTER
        step = 0.005
        if level >= FULL_DB:                  # gone: until it is inaudible
            while t > 0.0 and env.held(t - step) >= AUDIBLE_DB:
                t -= step
        else:
            while t > 0.0 and abs(env.held(t - step) - level) <= 0.3:
                t -= step
        worst = max(worst, t)
    return worst


# -- the operators -------------------------------------------------------------------------
def _operators(patch: FmPatch, incs, amps, n: int, state=None, phase0=None):
    """The summed output of the carriers over `n` frames. `incs` are cycles a
    frame for each operator; `amps` the amplitude per frame of each.
    -> (output, feedback state (y1, y2))."""
    sin = math.sin
    two_pi = 2.0 * math.pi
    topology = ALGORITHMS[patch.algorithm]
    outs = [None] * 4
    start = phase0 or (0.0, 0.0, 0.0, 0.0)
    y1, y2 = state or (0.0, 0.0)
    for k in range(4):
        step = two_pi * incs[k]
        offset = two_pi * start[k]
        mods = topology["mods"].get(k, [])
        if k == 0:
            out = [0.0] * n
            scale = FEEDBACK[patch.feedback]
            a = amps[0]
            if scale == 0.0:
                out = [a[i] * sin(offset + step * i) for i in range(n)]
            else:
                for i in range(n):
                    v = a[i] * sin(offset + step * i + scale * 0.5 * (y1 + y2))
                    y2, y1 = y1, v
                    out[i] = v
            outs[0] = out
            continue
        a = amps[k]
        if not mods:
            outs[k] = [a[i] * sin(offset + step * i) for i in range(n)]
            continue
        if len(mods) == 1:
            m = outs[mods[0]]
        else:
            m = [sum(vals) for vals in zip(*[outs[j] for j in mods])]
        outs[k] = [a[i] * sin(offset + step * i + MODULATION * m[i])
                   for i in range(n)]
    carriers = topology["carriers"]
    if len(carriers) == 1:
        total = outs[carriers[0]]
    else:
        total = [sum(vals) for vals in zip(*[outs[j] for j in carriers])]
    return total, (y1, y2)


def _multiple(op: Operator) -> float:
    return 0.5 if op.mul == 0 else float(op.mul)


def frequencies(patch: FmPatch, f0: float):
    """Hz of each operator at a fundamental: the multiple and the detune."""
    return [f0 * _multiple(op) * (1.0 + op.dt * DT_RATIO) for op in patch.ops]


def render_note(patch: FmPatch, f0: float, seconds: float,
                rate: float = 44100.0, note: int = 49,
                gate: Optional[float] = None) -> List[float]:
    """The voice for `seconds`, from key-on; key-off at `gate` seconds
    (never, if None). Floats, unnormalised (a unit operator is 1.0)."""
    bad = patch.problems()
    if bad:
        raise FmError("; ".join(bad[:3]))
    n = int(seconds * rate)
    envs = [Envelope(op, note, gate) for op in patch.ops]
    amps = [e.amplitude(n, rate) for e in envs]
    incs = [f / rate for f in frequencies(patch, f0)]
    out, _ = _operators(patch, incs, amps, n)
    return out


# -- a voice held -----------------------------------------------------------------------------
def loop_cycles(patch: FmPatch, f0: float, frames: int,
                rate: float = 44100.0) -> int:
    """Cycles of the fundamental a loop of `frames` should hold: the nearest
    whole number, and an even one if an operator has a multiple of one half."""
    m = max(1, int(round(f0 * frames / rate)))
    if any(op.mul == 0 for op in patch.ops) and m % 2:
        m += 1
    return m


def operator_cycles(patch: FmPatch, cycles: int, long: bool):
    """Whole cycles of the loop for each operator. A detune moves an operator
    by whole bins: DT's share of the operator's frequency, in bins, rounded
    (so the same detune is a bin at A4 and none at A1, where a bin is 25
    cents: a loop cannot beat more slowly than its own length). A short loop
    has no bins to spare at all."""
    out = []
    for op in patch.ops:
        base = int(round(_multiple(op) * cycles))
        bins = int(round(op.dt * DT_RATIO * base)) if long and op.dt else 0
        out.append(max(1, base + bins))
    return out


def beats(patch: FmPatch, cycles: int) -> bool:
    """Does a loop of this many cycles of the fundamental have room for
    the voice's detune?"""
    return operator_cycles(patch, cycles, True) \
        != operator_cycles(patch, cycles, False)


def render_steady(patch: FmPatch, cycles: int, frames: int, intro: int,
                  rate: float, note: int = 49, long: bool = False):
    """-> (intro frames, loop frames): the voice held. `rate` is the frames a
    second the loop is played at (the loop holds `cycles` cycles of the
    fundamental in `frames`, so the fundamental is cycles * rate / frames).
    The envelope runs in real time through the intro and is handed over to its
    sustain level, so the loop starts where the intro ends."""
    bad = patch.problems()
    if bad:
        raise FmError("; ".join(bad[:3]))
    levels = steady_levels(patch, note)
    if levels is None:
        raise FmError("this voice does not hold a level; render it as a note")
    cyc = operator_cycles(patch, cycles, long)
    incs = [c / float(frames) for c in cyc]
    envs = [Envelope(op, note, None) for op in patch.ops]
    steady_amps = [e.amplitude(1, rate, frozen_db=level)[0]
                   for e, level in zip(envs, levels)]
    # the feedback settles over a few dozen cycles of the fundamental
    roll = PRE_ROLL_CYCLES * frames // max(1, cycles)
    flat = [[a] * roll for a in steady_amps]
    _, state = _operators(patch, incs, flat, roll)
    # the steady voice from time zero, phases running on into the loop
    total = intro + frames
    held = [[a] * total for a in steady_amps]
    steady, _ = _operators(patch, incs, held, total, state)
    if intro <= 0:
        return [], steady[intro:]
    real_amps = [e.amplitude(intro, rate) for e in envs]
    real, _ = _operators(patch, incs, real_amps, intro, state)
    fade = max(1, min(intro // 2, int(0.03 * rate)))
    start = intro - fade
    head = real[:start] + [
        real[i] * (1.0 - w) + steady[i] * w
        for i, w in ((start + k, 0.5 - 0.5 * math.cos(math.pi * (k + 1) / fade))
                     for k in range(fade))]
    return head, steady[intro:]
