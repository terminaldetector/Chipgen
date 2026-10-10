"""ym3812.py — DNA to an AdLib / Sound Blaster (OPL2) voice.

Two operators, one connection bit, four waveforms, and no detune at all.
That last fact decides the shape of this compiler: what a four-operator
chip does with a spare carrier, this chip does with a second *channel*
(a layer, a few cents sharp, a few flat), and what the YM2612 does with
algorithms this chip does with waveforms.

Rate tables, measured on opl2.py:

    attack  ms   = 1250 * 2^-(AR - 1)               (10% .. 80%, x8/7)
    decay   dB/s = 11.8 * 2^(DR - 3)
    release ms   = 1698 * 2^-(RR - 3)               (to -20 dB)

Waveform numbers are the chip's: 0 sine, 1 half-sine (positive half only),
2 absolute sine (an octave up in disguise), 3 pulse-sine (quarter cycles).
"""

import math

import opl2 as opl2_chip
from opl2 import OPLInstrument, OPLOperator
import opl_instruments
import events as E

from ..dna import DNA
from ..genome import Genome
from . import common
from .base import Backend, Compiled, in_range, register_backend

#: register value -> multiple (the chip's doubled table, halved)
MULTIPLES = (0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 12, 12, 15, 15)
#: register values of the multiples, in order of how clangorous they are
CLANG_REGISTERS = (1, 2, 3, 5, 7, 9, 12, 14)


def _clip(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def attack_ms(ar: int) -> float:
    return 1250.0 * 2.0 ** (-(ar - 1))


def decay_db_per_s(dr: int) -> float:
    return 11.8 * 2.0 ** (dr - 3)


def release_ms(rr: int) -> float:
    return 1698.0 * 2.0 ** (-(rr - 3))


def ar_for_attack(axis: float) -> int:
    ms = common.attack_seconds(axis) * 1000.0
    if ms < 1.5:
        return 15
    return int(round(_clip(1.0 - math.log2(ms / 1250.0), 1, 15)))


def dr_for_slope(slope: float) -> int:
    return int(round(_clip(3.0 + math.log2(max(slope, 0.1) / 11.8), 1, 15)))


def rr_for_release(axis: float) -> int:
    seconds = common.release_seconds(axis)
    return int(round(_clip(3.0 - math.log2(seconds * 1000.0 / 1698.0), 1, 15)))


def choose_mwave(d: DNA) -> int:
    """Sine for a dark sound, half-sine and then pulse-sine as it brightens
    (each puts more harmonics into the same modulation depth)."""
    attack_b = _clip(d.brightness + 0.5 * d.punch)
    return 0 if attack_b < 0.45 else 1 if attack_b < 0.8 else 3


def choose_cwave(d: DNA) -> int:
    return 0 if d.brightness < 0.7 else 1


def compile_genome(genome: Genome) -> Compiled:
    d = genome.effective()
    style = genome.style
    notes = []
    pinned = style.get("conn", "auto")
    if pinned in ("fm", "additive"):
        additive = pinned == "additive"
    elif pinned == "auto":
        additive = ((d.body >= 0.8 and d.brightness <= 0.35
                     and d.metallicity < 0.35)
                    or d.bass_weight >= 0.85 and d.brightness <= 0.4)
    sustained_b = _clip(d.brightness - 0.35 * d.punch)
    attack_b = _clip(d.brightness + 0.5 * d.punch)

    # the modulator's level is the index, hence the brightness
    mod_tl = int(round(_clip(60.0 - 56.0 * attack_b ** 0.85, 0, 63)))
    feedback = int(round(7 * _clip(attack_b) ** 1.5))
    mwave = style.get("mwave", "auto")
    if mwave == "auto":
        mwave = choose_mwave(d)
    index = int(round(d.metallicity * (len(CLANG_REGISTERS) - 1)))
    if d.metallicity < 0.15:
        mod_reg = 1
    else:
        mod_reg = CLANG_REGISTERS[max(1, index)]
    car_reg = 0 if d.bass_weight >= 0.8 and d.brightness < 0.5 else 1
    cwave = style.get("cwave", "auto")
    if cwave == "auto":
        cwave = choose_cwave(d)
    if additive:
        # both operators are heard, so the "modulator" is a harmonic
        mod_reg = 2 if d.brightness < 0.6 else 3
        mod_tl = int(round(_clip(14.0 + 40.0 * (1.0 - d.brightness)
                                 + 14.0 * d.bass_weight, 0, 63)))
        mwave = 0 if d.brightness < 0.6 else 1
        feedback = int(round(5 * d.brightness ** 1.3))
        notes.append("additive connection: both operators reach the output")
    else:
        notes.append("FM connection: the modulator bends the carrier")

    ar = ar_for_attack(d.attack)
    plan = common.envelope_plan(d.body, d.decay)
    sustaining = plan["hold"] or plan["level"] >= 0.06
    if plan["hold"]:
        dr, sl = 0, 0
    else:
        dr = dr_for_slope(plan["slope"])
        sl = common.db_steps(plan["floor"], 3.0, 15)
    rr = rr_for_release(d.articulation)
    punch_steps = int(round(d.punch * 10))
    motion = d.motion
    vibrato = 1 if motion >= 0.12 else 0
    tremolo = 1 if motion >= 0.45 else 0

    carrier = OPLOperator(
        attack=ar, decay=dr, sustain_level=sl, release=rr, total_level=0,
        multiple=car_reg, waveform=cwave, sustaining=sustaining,
        tremolo=tremolo, vibrato=vibrato)
    # The modulator's fall is the "bright at the front, plain after", so it
    # is the punch dial and nothing else: a modulator that follows the
    # carrier down makes every decaying sound punchy whatever was asked.
    # Its steps (SL, 3 dB each) are taken in about 45 ms; DR 13 would be
    # 2 ms, a click gone before the attack window opens.
    m_sl = min(15, punch_steps)
    m_dr = 0 if m_sl == 0 else dr_for_slope(3.0 * m_sl / 0.045)
    modulator = OPLOperator(
        attack=ar, decay=min(15, m_dr), sustain_level=m_sl, release=rr,
        total_level=mod_tl, multiple=mod_reg, waveform=mwave,
        sustaining=sustaining or m_sl == 0, tremolo=0, vibrato=vibrato)
    patch = OPLInstrument(modulator, carrier, feedback=feedback,
                          connection=1 if additive else 0,
                          name=genome.name or "forge")

    setup = {}
    if vibrato or tremolo:
        setup = {"vibrato_depth": 1 if motion >= 0.6 else 0,
                 "tremolo_depth": 1 if motion >= 0.8 else 0}
        notes.append("operator vibrato" + (" and tremolo" if tremolo else ""))
    layers = []
    if d.detune >= 0.03:
        ratio = common.detune_ratio(d.detune)
        cents = round(common.detune_cents(genome.register or ("A", 3)), 1)
        layers = [{"cents": cents,
                   "level": round(20.0 * math.log10(ratio), 1)}]
        notes.append(f"detune is one extra channel {cents} cents sharp and "
                     f"{-20.0 * math.log10(ratio):.0f} dB down (this chip "
                     f"has no detune register)")
    gate = 0.45 + 0.5 * d.articulation
    style_out = {"gate": round(min(1.0, gate), 3),
                 "retrigger": d.articulation < 0.85, "layers": layers}
    notes.append(f"attack AR {ar} (~{attack_ms(ar):.0f} ms), release RR {rr} "
                 f"(~{release_ms(rr):.0f} ms to -20 dB)")
    return Compiled(backend="opl2", name=genome.name or "forge", patch=patch,
                    setup=setup, style=style_out, notes=notes)


#: a Portamento fast enough to be a jump (nes_driver.JUMP_RATE)
JUMP_RATE = 48000.0


def layer_velocity(velocity: int, layer: dict) -> int:
    """The velocity a detune layer is keyed at: velocity is amplitude-linear
    on this chip, so a level in dB is a factor on it."""
    return max(1, min(127, int(round(velocity * 10 ** (
        layer.get("level", 0) / 20.0)))))


class YM3812Backend(Backend):
    name = "opl2"
    chip = "YM3812 (OPL2, AdLib / Sound Blaster)"
    default_register = ("A", 3)
    supports = frozenset(("brightness", "attack", "body", "decay",
                          "metallicity", "detune", "motion", "bass_weight",
                          "articulation", "punch"))
    genes = {"conn": ["auto", "fm", "additive"],
             "mwave": ["auto", 0, 1, 2, 3], "cwave": ["auto", 0, 1, 2, 3]}
    channels = 9

    def compile(self, genome):
        return compile_genome(self.clamp_genome(genome))

    def pin(self, genome):
        genome = self.clamp_genome(genome)
        d = genome.effective()
        style = dict(genome.style)
        if style.get("conn", "auto") == "auto":
            additive = ((d.body >= 0.8 and d.brightness <= 0.35
                         and d.metallicity < 0.35)
                        or d.bass_weight >= 0.85 and d.brightness <= 0.4)
            style["conn"] = "additive" if additive else "fm"
        # the waveforms are chosen from the intent, once: left to follow
        # the corrected brightness they would switch mid-correction and the
        # brightness would jump
        genome0 = genome.clone(bias={})
        d0 = genome0.effective()
        if style.get("mwave", "auto") == "auto":
            style["mwave"] = choose_mwave(d0)
        if style.get("cwave", "auto") == "auto":
            style["cwave"] = choose_cwave(d0)
        return genome.clone(style=style)

    @property
    def voices_per_note(self):                       # noqa: D401
        return 1

    def voices_for(self, compiled) -> int:
        return 1 + len(compiled.style.get("layers", ()))

    def render(self, compiled, note, octave, velocity=127, hold=1.0,
               tail=0.5, ensemble=True):
        chip = opl2_chip.YM3812()
        s = compiled.setup
        chip.tremolo_depth = s.get("tremolo_depth", 0)
        chip.vibrato_depth = s.get("vibrato_depth", 0)
        layers = compiled.style.get("layers", ()) if ensemble else ()
        used = [0] + list(range(1, 1 + len(layers)))
        for ch in used:
            chip.set_instrument(ch, compiled.patch)
        chip.note_on(0, note, octave, velocity=velocity)
        for k, layer in enumerate(layers, 1):
            chip.note_on(k, note, octave,
                         velocity=layer_velocity(velocity, layer))
            chip.set_pitch_offset(k, layer["cents"])
        held = list(chip.render(int(chip.native_rate * hold)))
        for ch in used:
            chip.note_off(ch)
        released = list(chip.render(int(chip.native_rate * tail)))
        return held + released, chip.native_rate

    def setup_events(self, compiled, channel):
        out = []
        for k in range(self.voices_for(compiled)):
            out.append(E.OPLInstrumentSelect(channel=channel + k,
                                             instrument=compiled.name))
        s = compiled.setup
        if s:
            out.append(E.OPLDepth(tremolo=s.get("tremolo_depth", 0),
                                  vibrato=s.get("vibrato_depth", 0)))
        return out

    def events(self, compiled, channel, note, octave, length_ticks, velocity,
               ticks_per_second):
        gate = max(1, int(round(length_ticks * compiled.style.get("gate", 1.0))))
        out = [(0, E.OPLNoteOn(channel=channel, note=note, octave=octave,
                               velocity=velocity)),
               (gate, E.OPLNoteOff(channel=channel))]
        return out + self.layer_events(compiled, channel, note, octave,
                                       velocity, gate)

    def layer_events(self, compiled, channel, note, octave, velocity,
                     length_ticks):
        """The extra channels' share of one note: [(tick offset, Event)].
        The pitch offset is a Portamento fast enough to be a jump, undone
        with the note, so the channel is clean for whatever uses it next."""
        out = []
        for k, layer in enumerate(compiled.style.get("layers", ()), 1):
            level = layer_velocity(velocity, layer)
            target = f"opl{channel + k}"
            out.append((0, E.OPLNoteOn(channel=channel + k, note=note,
                                       octave=octave, velocity=level)))
            out.append((0, E.Portamento(target=target,
                                        cents_per_second=JUMP_RATE,
                                        to_cents=layer["cents"])))
            out.append((length_ticks, E.OPLNoteOff(channel=channel + k)))
            out.append((length_ticks, E.Portamento(
                target=target, cents_per_second=JUMP_RATE, to_cents=0.0)))
        return out

    def install(self, compiled, character=""):
        from .. import registry
        compiled.patch.name = compiled.name
        opl_instruments.add(compiled.patch)
        registry.register(self.name, compiled)
        return compiled.name

    def reference_level(self):
        if "level" not in _REF:
            from . import ym3812 as _self
            probe_patch = opl_instruments.get("opl_organ")
            c = Compiled("opl2", "opl_organ", probe_patch)
            from ..probe import window_rms
            mono, rate = self.render(c, "A", 3, 127, 0.8, 0.4)
            _REF["level"] = window_rms(mono, rate)
        return _REF["level"]

    def set_trim(self, compiled, steps):
        compiled.patch.trim = int(steps)

    def validate(self, compiled):
        p = compiled.patch
        problems = []
        in_range(problems, "feedback", p.feedback, 0, 7)
        in_range(problems, "connection", p.connection, 0, 1)
        for label, op in (("modulator", p.modulator), ("carrier", p.carrier)):
            for field, low, high in (
                    ("total_level", 0, 63), ("attack", 0, 15),
                    ("decay", 0, 15), ("sustain_level", 0, 15),
                    ("release", 0, 15), ("multiple", 0, 15),
                    ("waveform", 0, 3)):
                in_range(problems, f"{label} {field}", getattr(op, field),
                         low, high)
        for k, layer in enumerate(compiled.style.get("layers", ()), 1):
            in_range(problems, f"layer {k} cents", layer.get("cents"), -100, 100)
            in_range(problems, f"layer {k} level", layer.get("level"), -60, 6)
        for key in ("vibrato_depth", "tremolo_depth"):
            if key in compiled.setup:
                in_range(problems, key, compiled.setup[key], 0, 1)
        return problems

    def to_dict(self, compiled):
        p = compiled.patch

        def op(o):
            return {f: (bool(getattr(o, f)) if f == "sustaining"
                        else getattr(o, f)) for f in OPLOperator.__slots__}
        return {"patch": {"name": compiled.name, "feedback": p.feedback,
                          "connection": p.connection, "trim": p.trim,
                          "modulator": op(p.modulator),
                          "carrier": op(p.carrier)},
                "setup": dict(compiled.setup), "style": dict(compiled.style),
                "notes": list(compiled.notes)}

    def from_dict(self, data):
        p = data["patch"]

        def op(d):
            return OPLOperator(**{f: d[f] for f in OPLOperator.__slots__
                                  if f in d})
        patch = OPLInstrument(op(p["modulator"]), op(p["carrier"]),
                              feedback=p["feedback"],
                              connection=p["connection"], name=p["name"],
                              trim=p.get("trim", 0))
        return Compiled("opl2", p["name"], patch, dict(data.get("setup", {})),
                        dict(data.get("style", {})), list(data.get("notes", [])))


_REF = {}
register_backend(YM3812Backend())
