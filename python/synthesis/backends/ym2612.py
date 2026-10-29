"""ym2612.py — DNA to a Sega Mega Drive FM voice.

Four operators, eight algorithms, a feedback loop on operator 1 and a
per-channel LFO. The compiler works in the order a patch designer does:

  1. pick the *structure* (algorithm) the DNA needs — how many carriers,
     whether modulators stack;
  2. place the carriers (pitch, level, the twin that makes a chorus, the
     sub-octave that makes a bass) and the modulators (their level is the
     brightness);
  3. set the envelopes from the time axes using the chip's own rate tables,
     which were measured, not remembered:

        attack  ms  = 223 * 2^(-(AR - 10)/2)           (10% .. 80%, x8/7)
        decay   dB/s = 16.7 * 2^((D1R - 10)/2)
        release ms  = 1740 * 2^(-(RR - 4))             (to -20 dB)

  4. set the LFO depth for motion (a channel setting, so it travels as
     `setup`, not inside the patch).

It does not have to be exact. `evolve.realise` plays the result, reads the
dials back and nudges the biases until they agree, so the formulas below
only have to be monotone and roughly right; what makes them right is the
measurement loop, and what keeps them honest is tests/test_synthesis.py,
which sweeps every axis and fails if the reading does not follow.
"""

import math

import opn2
from opn2 import Operator
import instruments as instruments_mod
import events as E

from ..dna import DNA
from ..genome import Genome, slug
from . import common
from .base import Backend, Compiled, in_range, register_backend

#: op -> (role, ops it modulates), ordinary 1..4 numbering
PLANS = {
    0: {1: ("mod", (2,)), 2: ("mod", (3,)), 3: ("mod", (4,)), 4: ("car", ())},
    1: {1: ("mod", (3,)), 2: ("mod", (3,)), 3: ("mod", (4,)), 4: ("car", ())},
    2: {1: ("mod", (4,)), 2: ("mod", (3,)), 3: ("mod", (4,)), 4: ("car", ())},
    3: {1: ("mod", (2,)), 2: ("mod", (4,)), 3: ("mod", (4,)), 4: ("car", ())},
    4: {1: ("mod", (2,)), 2: ("car", ()), 3: ("mod", (4,)), 4: ("car", ())},
    5: {1: ("mod", (2, 3, 4)), 2: ("car", ()), 3: ("car", ()),
        4: ("car", ())},
    6: {1: ("mod", (2,)), 2: ("car", ()), 3: ("car", ()), 4: ("car", ())},
    7: {1: ("car", ()), 2: ("car", ()), 3: ("car", ()), 4: ("car", ())},
}

#: modulator multiples in order of how clangorous they make a sound
CLANG_MULTIPLES = (1, 2, 3, 5, 7, 9, 11, 13, 14, 15)
#: the global LFO rates, Hz, by register value
LFO_HZ = opn2.LFO_FREQUENCIES
#: peak vibrato depth by PMS, in cents (YM2612 application manual)
PMS_CENTS = (0.0, 3.4, 6.7, 10.0, 14.0, 20.0, 40.0, 80.0)


def _clip(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


# ---- the measured rate tables, inverted ------------------------------------
def attack_ms(ar: int) -> float:
    return 223.0 * 2.0 ** (-(ar - 10) / 2.0)


def decay_db_per_s(rate: int) -> float:
    return 16.7 * 2.0 ** ((rate - 10) / 2.0)


def release_ms(rr: int) -> float:
    return 1740.0 * 2.0 ** (-(rr - 4))


def ar_for_attack(axis: float) -> int:
    """The attack axis, in time, to the nearest attack rate."""
    ms = common.attack_seconds(axis) * 1000.0
    if ms < 0.6:
        return 31
    return int(round(_clip(10.0 - 2.0 * math.log2(ms / 223.0), 1, 31)))


def rate_for_slope(slope: float) -> int:
    """The decay (or sustain) rate that falls `slope` dB per second."""
    return int(round(_clip(10.0 + 2.0 * math.log2(max(slope, 0.1) / 16.7),
                           0, 31)))


def rr_for_release(axis: float) -> int:
    seconds = common.release_seconds(axis)
    return int(round(_clip(4.0 - math.log2(seconds * 1000.0 / 1740.0), 0, 15)))


# ---- structure ----------------------------------------------------------------
def choose_algorithm(d: DNA, style: dict) -> int:
    pinned = style.get("alg", "auto")
    if pinned != "auto":
        return int(pinned)
    if d.bass_weight >= 0.8 and d.brightness <= 0.3:
        # a sub bass wants a carrier of its own
        if d.metallicity >= 0.55 and d.brightness >= 0.45:
            return 4
        if d.brightness >= 0.55 and d.body < 0.7:
            return 4
        return 5 if d.brightness >= 0.4 else 7
    if d.metallicity >= 0.6:
        return 3 if d.brightness >= 0.5 else 4
    if d.brightness >= 0.7:
        return 0
    if d.brightness <= 0.25 and d.metallicity < 0.3:
        return 7
    if d.body >= 0.8 and d.attack >= 0.35:
        return 5
    return 4


def _depths(plan):
    """How many modulators sit between each operator and the output."""
    memo = {}

    def depth(op):
        if op in memo:
            return memo[op]
        role, feeds = plan[op]
        memo[op] = 0 if role == "car" else 1 + max(depth(t) for t in feeds)
        return memo[op]
    return {op: depth(op) for op in plan}


def _ratio_set(d: DNA, style: dict):
    pinned = style.get("ratios", "auto")
    if pinned != "auto":
        return pinned
    if d.metallicity >= 0.7:
        return "clang"
    if d.metallicity >= 0.4:
        return "bell"
    return "harmonic"


def compile_genome(genome: Genome) -> Compiled:
    d = genome.effective()
    style = genome.style
    alg = choose_algorithm(d, style)
    plan = PLANS[alg]
    plan_roles = plan
    depth = _depths(plan)
    ratios = _ratio_set(d, style)
    notes = [f"algorithm {alg}"]

    carriers = [op for op in (1, 2, 3, 4) if plan[op][0] == "car"]
    mods = [op for op in (1, 2, 3, 4) if plan[op][0] == "mod"]
    ops = {op: {"mul": 1, "dt": 0, "tl": 0} for op in (1, 2, 3, 4)}

    # ---- modulators: their level IS the brightness ---------------------------
    sustained_b = _clip(d.brightness - 0.35 * d.punch)
    attack_b = _clip(d.brightness + 0.5 * d.punch)
    for op in mods:
        base = 56.0 - 52.0 * (attack_b ** 0.85)
        extra = 4.0 * (depth[op] - 1) + (3.0 if sum(
            1 for o in plan if op in plan[o][1]) > 1 else 0.0)
        # a modulator that feeds several carriers is heard several times
        fan = len(plan[op][1])
        extra += 2.0 * (fan - 1)
        ops[op]["tl"] = int(round(_clip(base + extra, 0, 127)))
    clang_index = int(round(d.metallicity * (len(CLANG_MULTIPLES) - 1)))
    for slot, op in enumerate(sorted(mods, key=lambda o: -depth[o])):
        if ratios == "harmonic":
            mul = (1, 1, 2)[min(slot, 2)] if d.metallicity < 0.2 else \
                CLANG_MULTIPLES[min(clang_index, 4)]
        elif ratios == "bell":
            mul = CLANG_MULTIPLES[max(2, min(clang_index + slot, 8))]
        elif ratios == "clang":
            mul = CLANG_MULTIPLES[min(clang_index + slot, 9)]
        else:                                    # "octave"
            mul = (1, 2, 4)[min(slot, 2)]
        ops[op]["mul"] = mul
    # ---- carriers ---------------------------------------------------------------
    primary = carriers[0]
    ops[primary].update(mul=1, tl=0)
    spare = carriers[1:]
    # (Detune is a second channel, below. DT1 would be free but moves the
    # pitch by a fraction of a hertz at A3, too slow to be heard as a
    # chorus; measured, not assumed.)
    if spare and d.bass_weight >= 0.6:
        sub = spare.pop(0)
        ops[sub].update(mul=0, tl=int(round(18 - 14 * d.bass_weight)))
        notes.append(f"sub-octave carrier on operator {sub}")
    harmonic_slot = 2
    color = _clip(max(d.brightness - 0.25, d.metallicity))
    for op in spare:
        if ratios in ("bell", "clang"):
            mul = CLANG_MULTIPLES[min(2 + int(color * 4) + harmonic_slot - 2, 8)]
        else:
            mul = harmonic_slot
        weight_db = 3.0 + 28.0 * (1.0 - color) + 14.0 * d.bass_weight
        ops[op].update(mul=mul, tl=int(round(_clip(weight_db / 0.75, 0, 127))))
        harmonic_slot += 1
    # bass weight is a body voice against its colour. Above the middle the
    # siblings of the primary carrier drop away and whatever modulates the
    # primary is held back, so the fundamental stays clean; below it the
    # primary itself steps back, which is how a bell or a glassy stab gets
    # its fundamental out of the way
    balance = d.bass_weight - 0.4
    if balance > 0 and len(carriers) > 1:
        drop = int(round(30.0 * (balance / 0.6) ** 1.2 * 0.8))
        for op in carriers:
            if op != primary and ops[op]["mul"] != 0:
                ops[op]["tl"] = int(_clip(ops[op]["tl"] + drop, 0, 127))
        for op in mods:
            if primary in plan[op][1] and not any(
                    t != primary for t in plan[op][1]):
                ops[op]["tl"] = int(_clip(
                    ops[op]["tl"] + 16.0 * (balance / 0.6), 0, 127))
    elif balance < 0 and len(carriers) > 1:
        ops[primary]["tl"] = int(_clip(ops[primary]["tl"]
                                       + 52.0 * (-balance / 0.4), 0, 100))

    # ---- feedback ---------------------------------------------------------------
    if plan[1][0] == "mod":
        feedback = int(round(7 * _clip(attack_b) ** 1.5))
    else:
        feedback = int(round(5 * _clip(d.brightness) ** 1.3))
    feedback = int(_clip(feedback + (1 if d.metallicity > 0.6 else 0), 0, 7))

    # ---- envelopes ---------------------------------------------------------------
    ar = ar_for_attack(d.attack)
    plan = common.envelope_plan(d.body, d.decay)
    if plan["hold"]:
        d1r_car, d1l, d2r = 0, 0, 0
    else:
        d1r_car = rate_for_slope(plan["slope"])
        d1l = common.db_steps(plan["floor"], 3.0, 15)
        # what is left after the fall keeps falling slowly, so a pluck dies
        d2r = 0 if plan["level"] >= 0.2 else 5
    rr = rr_for_release(d.articulation)
    punch_steps = int(round(d.punch * 10))
    built = {}
    for op in (1, 2, 3, 4):
        role = plan_roles[op][0]
        spec = ops[op]
        if role == "car":
            built[op] = Operator(
                detune=spec["dt"], multiple=spec["mul"], total_level=spec["tl"],
                attack_rate=ar, decay_rate=d1r_car, sustain_rate=d2r,
                release_rate=rr, sustain_level=d1l)
        else:
            m_d1l = max(punch_steps, int(round(d1l * 0.6)))
            m_d1r = 0 if m_d1l == 0 else max(d1r_car, 10 + punch_steps)
            built[op] = Operator(
                detune=spec["dt"], multiple=spec["mul"], total_level=spec["tl"],
                attack_rate=ar, decay_rate=min(31, m_d1r),
                sustain_rate=0 if plan["hold"] else d2r,
                release_rate=rr, sustain_level=min(15, m_d1l))

    # ---- motion: the LFO is a channel setting -------------------------------------
    setup = {}
    if d.motion >= 0.08:
        pms = int(_clip(round(1 + d.motion * 6), 1, 7))
        # AMS 3 is a 23.6 dB tremolo; two steps (5.9 dB) is as far as
        # an instrument, as against an effect, wants to go
        ams = int(_clip(round((d.motion - 0.4) * 4), 0, 2))
        freq = 4 if d.motion < 0.6 else 5
        setup = {"pms": pms, "ams": ams, "lfo": freq}
        if ams:
            for op in carriers:
                built[op].am_enable = 1
        notes.append(f"LFO {LFO_HZ[freq]:.1f} Hz, vibrato +-{PMS_CENTS[pms]:g}"
                     f" cents, tremolo {ams}")

    patch = instruments_mod.patch(genome.name or "forge", alg, feedback,
                                  built[1], built[2], built[3], built[4])
    gate = 0.45 + 0.5 * d.articulation
    layers = []
    if d.detune >= 0.03:
        ratio = common.detune_ratio(d.detune)
        cents = round(common.detune_cents(genome.register or ("A", 3)), 1)
        layers = [{"cents": cents,
                   "level": round(20.0 * math.log10(ratio), 1)}]
        notes.append(f"detune is one extra channel {cents} cents sharp and "
                     f"{-20.0 * math.log10(ratio):.0f} dB down")
    style_out = {"gate": round(min(1.0, gate), 3),
                 "retrigger": d.articulation < 0.85, "layers": layers}
    notes.append(f"attack AR {ar} (~{attack_ms(ar):.0f} ms), "
                 f"release RR {rr} (~{release_ms(rr):.0f} ms to -20 dB)")
    return Compiled(backend="ym2612", name=genome.name or "forge",
                    patch=patch, setup=setup, style=style_out, notes=notes)


# ---- the backend ---------------------------------------------------------------
#: a Portamento fast enough to be a jump (nes_driver.JUMP_RATE)
JUMP_RATE = 48000.0


def layer_velocity(velocity: int, layer: dict) -> int:
    """The velocity a detune layer is keyed at: velocity is amplitude-linear
    on the FM bank, so a level in dB is a factor on it."""
    return max(1, min(127, int(round(velocity * 10 ** (
        layer.get("level", 0) / 20.0)))))


class YM2612Backend(Backend):
    name = "ym2612"
    chip = "YM2612 (Sega Mega Drive / Genesis)"
    default_register = ("A", 3)
    supports = frozenset((
        "brightness", "attack", "body", "decay", "metallicity", "detune",
        "motion", "bass_weight", "articulation", "punch"))
    genes = {"alg": ["auto", 0, 1, 2, 3, 4, 5, 6, 7],
             "ratios": ["auto", "harmonic", "bell", "clang", "octave"]}
    channels = 6

    def compile(self, genome):
        return compile_genome(self.clamp_genome(genome))

    def pin(self, genome):
        genome = self.clamp_genome(genome)
        d = genome.effective()
        style = dict(genome.style)
        if style.get("alg", "auto") == "auto":
            style["alg"] = choose_algorithm(d, style)
        if style.get("ratios", "auto") == "auto":
            style["ratios"] = _ratio_set(d, style)
        return genome.clone(style=style)

    def voices_for(self, compiled) -> int:
        return 1 + len(compiled.style.get("layers", ()))

    def render(self, compiled, note, octave, velocity=127, hold=1.0,
               tail=0.5, ensemble=True):
        import analysis
        import audio
        chip = opn2.YM2612(chip_type="ym3438")
        s = compiled.setup
        layers = compiled.style.get("layers", ()) if ensemble else ()
        used = list(range(1 + len(layers)))
        for ch in used:
            chip.set_instrument(ch, compiled.patch)
            chip.set_pan(ch, True, True, s.get("ams", 0), s.get("pms", 0))
        if s:
            chip.set_lfo(True, s.get("lfo", 4))
        chip.note_on(0, note, octave, velocity=velocity)
        for k, layer in enumerate(layers, 1):
            chip.set_pitch_offset(k, layer["cents"])
            chip.note_on(k, note, octave,
                         velocity=layer_velocity(velocity, layer))
        held = chip.render(int(chip.native_rate * hold))
        for ch in used:
            chip.note_off(ch)
        released = chip.render(int(chip.native_rate * tail))
        rate = chip.native_rate
        chip.close()
        return list(analysis.to_mono(audio.concat([held, released]))), rate

    def setup_events(self, compiled, channel):
        s = compiled.setup
        out = []
        for k in range(self.voices_for(compiled)):
            out.append(E.FMInstrumentSelect(channel=channel + k,
                                            instrument=compiled.name))
            if s:
                out.append(E.FMPan(channel=channel + k, left=True, right=True,
                                   ams=s.get("ams", 0), pms=s.get("pms", 0)))
        if s:
            out.append(E.FMLFO(enable=True, freq=s.get("lfo", 4)))
        return out

    def events(self, compiled, channel, note, octave, length_ticks, velocity,
               ticks_per_second):
        gate = max(1, int(round(length_ticks * compiled.style.get("gate", 1.0))))
        out = [(0, E.FMNoteOn(channel=channel, note=note, octave=octave,
                              velocity=velocity)),
               (gate, E.FMNoteOff(channel=channel))]
        return out + self.layer_events(compiled, channel, note, octave,
                                       velocity, gate)

    def layer_events(self, compiled, channel, note, octave, velocity,
                     length_ticks):
        """The extra channels' share of one note: [(tick offset, Event)].
        The pitch offset is a Portamento fast enough to be a jump, undone
        with the note, so the channel is clean for whatever uses it next."""
        out = []
        for k, layer in enumerate(compiled.style.get("layers", ()), 1):
            target = f"fm{channel + k}"
            out.append((0, E.FMNoteOn(
                channel=channel + k, note=note, octave=octave,
                velocity=layer_velocity(velocity, layer))))
            out.append((0, E.Portamento(target=target,
                                        cents_per_second=JUMP_RATE,
                                        to_cents=layer["cents"])))
            out.append((length_ticks, E.FMNoteOff(channel=channel + k)))
            out.append((length_ticks, E.Portamento(
                target=target, cents_per_second=JUMP_RATE, to_cents=0.0)))
        return out

    def install(self, compiled, character=""):
        from .. import registry
        compiled.patch.name = compiled.name
        instruments_mod.add(compiled.patch, character)
        registry.register(self.name, compiled)
        return compiled.name

    def reference_level(self):
        """`organ`, the bank's median voice, played the way the probe plays
        everything (calibrate_bank measures through the full sequencer; the
        probe measures at the chip, so the reference must too)."""
        if "level" not in _REF:
            from ..probe import window_rms
            c = Compiled("ym2612", "organ", instruments_mod.get("organ"))
            mono, rate = self.render(c, "A", 3, 127, 0.8, 0.3)
            _REF["level"] = window_rms(mono, rate)
        return _REF["level"]

    def set_trim(self, compiled, steps):
        compiled.patch.trim = int(steps)

    def validate(self, compiled):
        p = compiled.patch
        problems = []
        in_range(problems, "algorithm", p.algorithm, 0, 7)
        in_range(problems, "feedback", p.feedback, 0, 7)
        for index, op in enumerate(p.operators, 1):
            for field, low, high in (
                    ("total_level", 0, 127), ("attack_rate", 0, 31),
                    ("decay_rate", 0, 31), ("sustain_rate", 0, 31),
                    ("release_rate", 0, 15), ("sustain_level", 0, 15),
                    ("multiple", 0, 15), ("detune", 0, 7)):
                in_range(problems, f"operator {index} {field}",
                         getattr(op, field), low, high)
        for k, layer in enumerate(compiled.style.get("layers", ()), 1):
            in_range(problems, f"layer {k} cents", layer.get("cents"), -100, 100)
            in_range(problems, f"layer {k} level", layer.get("level"), -60, 6)
        for key, high in (("pms", 7), ("ams", 3), ("lfo", 7)):
            if key in compiled.setup:
                in_range(problems, key, compiled.setup[key], 0, high)
        return problems

    def to_dict(self, compiled):
        d = instruments_mod.instrument_to_dict(compiled.patch)
        d["name"] = compiled.name
        return {"patch": d, "setup": dict(compiled.setup),
                "style": dict(compiled.style), "notes": list(compiled.notes)}

    def from_dict(self, data):
        patch = instruments_mod.instrument_from_dict(data["patch"])
        return Compiled(backend="ym2612", name=patch.name, patch=patch,
                        setup=dict(data.get("setup", {})),
                        style=dict(data.get("style", {})),
                        notes=list(data.get("notes", [])))


_REF = {}
register_backend(YM2612Backend())
