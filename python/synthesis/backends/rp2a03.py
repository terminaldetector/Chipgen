"""rp2a03.py — DNA to an NES / Famicom voice: macros, not registers.

The 2A03's pulse channel has a duty cycle (four settings), a 4-bit volume
and a period; the triangle has a period and nothing else; the noise
channel has a volume, a mode bit and sixteen fixed periods. Every musical
quality beyond that is the driver changing those once a frame, so the
compiler's output is a set of per-frame lists (`nes_driver.NESInstrument`):

    brightness  the duty cycle: 12.5% is the brightest pulse, 50% the
                warmest, and nothing on this chip is darker than a 50%
                pulse except the triangle
    attack      a volume ramp (the hardware has none: this is the driver)
    body, decay the volume decays to a sustain level and holds
    metallicity a fast arpeggio between pitches that clash
    detune      the same note on the other pulse channel, a few cents sharp
    motion      a vibrato in the pitch macro, and the duty cycling (PWM)
    punch       a pitch chirp and a bright duty for the first frames
    bass_weight the triangle, or the noise period index
    articulation the release tail and how much of the note the key is down

Where the chip simply cannot (a dark pulse, a quiet bass on the triangle),
the reading comes back near the limit and the scorer says so; it does not
pretend.
"""

import math

import nes_apu
import events as E

from ..dna import DNA
from ..genome import Genome
from .. import nes_driver as N
from . import common
from .base import Backend, Compiled, in_range, register_backend


def _clip(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


#: arpeggio steps by how clangorous the DNA is asked to be
ARP_SETS = ((0.3, (0, 12)), (0.55, (0, 7, 12)), (0.8, (0, 6, 13, 1)),
            (1.01, (0, 1, 6, 11, 13)))


def volume_macro(d: DNA, peak: int = 15) -> N.Macro:
    """The 4-bit volume over time: a ramp up, a fall to a floor, a hold at
    the floor, and a release tail."""
    attack_frames = int(round(common.attack_seconds(d.attack) * 60.0))
    values = []
    for i in range(attack_frames):
        values.append(max(1, round(peak * (i + 1) / (attack_frames + 1))))
    values.append(peak)
    plan = common.envelope_plan(d.body, d.decay)
    if plan["hold"]:
        sustain = peak
    else:
        floor = peak * plan["floor"]
        sustain = int(round(floor)) if floor >= 0.5 else 0
        for i in range(1, 120):
            amp = 10.0 ** (-plan["slope"] * (i / 60.0) / 20.0)
            v = int(round(max(floor, peak * amp)))
            values.append(v)
            if v <= sustain:
                break
        if sustain == 0:
            if values[-1] != 0:
                values.append(0)
            return N.Macro(values, loop=None, release=None)
    if values[-1] != sustain:
        values.append(sustain)
    sustain_at = len(values) - 1
    release_frames = max(1.0, common.release_seconds(d.articulation) * 60.0)
    tail = []
    k = 1
    while sustain > 0 and k < 90:
        v = int(round(sustain * 10.0 ** (-k / release_frames)))
        tail.append(v)
        if v == 0:
            break
        k += 1
    if not tail or tail[-1] != 0:
        tail.append(0)
    values += tail
    return N.Macro(values, loop=sustain_at, release=sustain_at + 1)


def compile_genome(genome: Genome) -> Compiled:
    d = genome.effective()
    style = genome.style
    notes = []
    kind = style.get("channel", "auto")
    if kind == "auto":
        if d.noise >= 0.6:
            kind = "noise"
        elif d.bass_weight >= 0.85 and d.brightness <= 0.3 and d.noise < 0.3:
            kind = "triangle"
        else:
            kind = "pulse"
    notes.append(f"{kind} channel")

    peak = 15

    if kind == "triangle":
        if d.body >= 0.5:
            volume = N.Macro([15, 0], loop=0, release=1)
        else:
            frames = max(2, int(round(common.decay_seconds(d.decay) * 60 * 1.8)))
            volume = N.Macro([15] * frames + [0], loop=None, release=None)
        pitch_values = [0.0]
        if d.punch > 0.1:
            pitch_values = [600.0 * d.punch, 250.0 * d.punch, 80.0 * d.punch,
                            0.0]
        pitch = N.Macro(pitch_values + [0.0],
                        loop=len(pitch_values) - 1,
                        release=len(pitch_values))
        inst = N.NESInstrument(genome.name or "forge", "triangle",
                               volume=volume, pitch=pitch)
        notes.append("the triangle has no volume: it is on or off")
    elif kind == "noise":
        volume = volume_macro(d, peak)
        base = int(round(_clip(15.0 - 11.0 * d.brightness
                               + 3.0 * d.bass_weight, 0, 15)))
        drop = int(round(4 * d.punch))
        period_vals = [max(0, base - drop), max(0, base - drop // 2), base]
        period = N.Macro(period_vals, loop=len(period_vals) - 1)
        inst = N.NESInstrument(genome.name or "forge", "noise", volume=volume,
                               period=period,
                               mode=1 if d.metallicity >= 0.5 else 0)
        notes.append(f"noise period {base}"
                     + (", metallic mode" if inst.mode else ""))
    else:
        volume = volume_macro(d, peak)
        # a pulse's fundamental is strongest at 50% and weakest at 12.5%, so a
        # heavy sound and a bright one pull the duty in opposite directions
        b_eff = d.brightness - 0.45 * d.bass_weight
        base_duty = 0 if b_eff >= 0.65 else 1 if b_eff >= 0.3 else 2
        cycle = [base_duty]
        if d.motion >= 0.45:
            period = max(4, int(round(12 - 8 * d.motion)))
            wobble = [2, 1, 0, 1] if base_duty <= 1 else [2, 1, 2, 3]
            cycle = [wobble[(i * 4 // period) % 4] for i in range(period)]
        lead_in = [0] * int(round(3 * d.punch)) if d.punch >= 0.15 else []
        duty = N.Macro(lead_in + cycle, loop=len(lead_in),
                       release=None)
        # pitch: a chirp, then (after a delay) vibrato; back to 0 on release
        chirp = []
        if d.punch >= 0.15:
            chirp = [600.0 * d.punch, 300.0 * d.punch, 100.0 * d.punch]
        vib = []
        if d.motion >= 0.1:
            depth = 4.0 + 36.0 * d.motion
            vib = [depth * math.sin(2 * math.pi * i / 10.0) for i in range(10)]
        delay = [0.0] * (6 if vib else 0)
        pitch_values = chirp + delay + (vib or [0.0])
        loop_at = len(chirp) + len(delay) if vib else len(pitch_values) - 1
        pitch = N.Macro(pitch_values + [0.0], loop=loop_at,
                        release=len(pitch_values))
        arp_values = [0]
        for limit, steps in ARP_SETS:
            if d.metallicity < limit:
                if d.metallicity >= 0.3:
                    arp_values = list(steps)
                break
        # a flash of octave-and-a-fifth up for the first two frames is the
        # NES's attack transient: brighter at the front, plain afterwards
        flash = [19, 12] if d.punch >= 0.15 else []
        flash = [round(x * min(1.0, d.punch * 1.5)) for x in flash]
        if len(arp_values) > 1:
            arp = N.Macro(flash + arp_values + [0], loop=len(flash),
                          release=len(flash) + len(arp_values))
        else:
            arp = N.Macro(flash + [0, 0], loop=len(flash) + 0,
                          release=len(flash) + 1) if flash else \
                N.Macro([0], loop=0)
        layers = []
        if d.detune >= 0.03:
            ratio = common.detune_ratio(d.detune)
            cents = round(common.detune_cents(genome.register or ("A", 3)), 1)
            layers.append({"cents": cents, "volume": round(ratio, 3),
                           "duty_shift": 0})
            notes.append(f"detune is the other pulse channel {cents} cents "
                         f"sharp at {ratio:.0%} of the volume")
        inst = N.NESInstrument(genome.name or "forge", "pulse", volume=volume,
                               duty=duty, pitch=pitch, arp=arp, layers=layers)
        notes.append(f"duty {('12.5', '25', '50', '75')[base_duty]}%"
                     + (" cycling" if d.motion >= 0.15 else ""))
    gate = 0.45 + 0.5 * d.articulation
    return Compiled("nes", genome.name or "forge", inst, {},
                    {"gate": round(min(1.0, gate), 3), "retrigger": True,
                     "channel": kind, "pitched": kind != "noise"}, notes)


class RP2A03Backend(Backend):
    name = "nes"
    chip = "RP2A03 (NES / Famicom)"
    default_register = ("A", 3)
    #: `punch` is built (a flash of octave-and-a-fifth and a pitch chirp for
    #: the first frames) but not scored: a 12 kHz-limited pulse spectrum has
    #: no brighter to get, so the spectral reading cannot see it.
    supports = frozenset(("brightness", "attack", "body", "decay",
                          "metallicity", "detune", "motion", "bass_weight",
                          "articulation", "noise"))
    genes = {"channel": ["auto", "pulse", "triangle", "noise"]}
    channels = 4

    def compile(self, genome):
        return compile_genome(self.clamp_genome(genome))

    def pin(self, genome):
        genome = self.clamp_genome(genome)
        d = genome.effective()
        style = dict(genome.style)
        if style.get("channel", "auto") == "auto":
            if d.noise >= 0.6:
                style["channel"] = "noise"
            elif d.bass_weight >= 0.85 and d.brightness <= 0.3 \
                    and d.noise < 0.3:
                style["channel"] = "triangle"
            else:
                style["channel"] = "pulse"
        return genome.clone(style=style)

    def voices_for(self, compiled) -> int:
        return 1 + len(compiled.patch.layers)

    def render(self, compiled, note, octave, velocity=127, hold=1.0,
               tail=0.5, ensemble=True):
        inst = compiled.patch
        apu = nes_apu.NESAPU()
        voices = nes_apu.Voices(apu)
        base = {"pulse": "pulse1", "triangle": "triangle",
                "noise": "noise"}[inst.channel]
        hold_frames = int(round(hold * 60))
        from opn2 import note_to_freq
        base_hz = note_to_freq(note, octave)
        plan = []
        for voice, layer in N.voices_for(inst, base)[:None if ensemble else 1]:
            plan.append((voice, N.frames(inst, hold_frames, velocity, layer,
                                         base_hz)))
        total = hold_frames + int(round(tail * 60))
        per_frame = apu.native_rate / 60.0
        owed = 0.0
        out = []
        state = {}
        for f in range(total):
            for voice, seq in plan:
                self._drive(voices, inst, voice, seq, f, note, octave, state)
            owed += per_frame
            n = int(owed)
            owed -= n
            if n:
                out.extend(apu.render(n).data[0::2])
        return _remove_dc(out, int(apu.native_rate * 0.025)), apu.native_rate

    @staticmethod
    def _drive(voices, inst, voice, seq, f, note, octave, state):
        if f > len(seq):
            return
        if f == len(seq):
            if inst.channel == "noise":
                voices.noise_off()
            else:
                voices.note_off(voice)
            return
        cur = seq[f]
        last = state.get(voice)
        vel = N_velocity(cur.volume)
        if f == 0:
            if inst.channel == "noise":
                voices.noise_on(cur.period, vel, metallic=bool(inst.mode))
            else:
                if inst.channel == "pulse":
                    voices.set_duty(voice, cur.duty)
                voices.note_on(voice, note, octave, velocity=vel)
                if abs(cur.cents) > 0.5:
                    voices.set_pitch_offset(voice, cur.cents)
        else:
            if cur.volume != last.volume:
                voices.set_volume(voice, vel)
            if inst.channel == "pulse" and cur.duty != last.duty:
                voices.set_duty(voice, cur.duty)
            if inst.channel == "noise":
                if cur.period != last.period:
                    voices.noise_on(cur.period, max(1, vel),
                                    metallic=bool(inst.mode))
            elif abs(cur.cents - last.cents) > 0.5:
                voices.set_pitch_offset(voice, cur.cents)
        state[voice] = cur

    def validate(self, compiled):
        inst = compiled.patch
        problems = []
        if inst.channel not in ("pulse", "triangle", "noise"):
            problems.append(f"channel is {inst.channel!r}; it is pulse, "
                            f"triangle or noise")
        for label, macro, low, high in (
                ("volume", inst.volume, 0, 15), ("duty", inst.duty, 0, 3),
                ("period", inst.period, 0, 15), ("arp", inst.arp, -48, 48),
                ("pitch", inst.pitch, -4800, 4800)):
            for i, value in enumerate(macro.values):
                in_range(problems, f"{label} frame {i}", value, low, high)
                if len(problems) > 8:
                    return problems
        for k, layer in enumerate(inst.layers, 1):
            if inst.channel != "pulse":
                problems.append("only a pulse instrument can have a layer")
            in_range(problems, f"layer {k} cents", layer.get("cents"), -100, 100)
            in_range(problems, f"layer {k} volume", layer.get("volume"), 0, 1.5)
        return problems

    def setup_events(self, compiled, channel):
        return []

    def events(self, compiled, channel, note, octave, length_ticks, velocity,
               ticks_per_second):
        inst = compiled.patch
        voice = channel if isinstance(channel, str) else \
            {"pulse": "pulse1", "triangle": "triangle",
             "noise": "noise"}[inst.channel]
        return N.to_events(inst, voice, note, octave, length_ticks, velocity,
                           ticks_per_second, compiled.style.get("gate", 1.0))

    def install(self, compiled, character=""):
        from .. import registry
        compiled.patch.name = compiled.name
        N.add(compiled.patch)
        # recorded so the placement pass can read the instrument's gate
        registry.register(self.name, compiled)
        return compiled.name

    def reference_level(self):
        if "level" not in _REF:
            from ..probe import window_rms
            from .base import Compiled as C
            pulse = N.NESInstrument("ref", "pulse",
                                    volume=N.Macro([12], loop=0, release=None),
                                    duty=N.constant(2))
            mono, rate = self.render(C("nes", "ref", pulse), "A", 3, 127,
                                     0.8, 0.2)
            _REF["level"] = window_rms(mono, rate)
        return _REF["level"]

    def set_trim(self, compiled, steps):
        compiled.patch.trim = int(max(-14, min(0, steps)))

    def to_dict(self, compiled):
        return {"patch": compiled.patch.to_dict(),
                "setup": dict(compiled.setup), "style": dict(compiled.style),
                "notes": list(compiled.notes)}

    def from_dict(self, data):
        patch = N.NESInstrument.from_dict(data["patch"])
        return Compiled("nes", patch.name, patch, dict(data.get("setup", {})),
                        dict(data.get("style", {})), list(data.get("notes", [])))


def _remove_dc(x, width: int):
    """Subtract a 25 ms running mean. The APU's mixer is unipolar, so its
    output sits on a DC level that follows the volume; a global mean (what
    the render path subtracts) would leave the silent tail sitting at
    minus that level, which reads as a release that never ends."""
    n = len(x)
    if n == 0 or width < 2:
        return list(x)
    prefix = [0.0]
    for v in x:
        prefix.append(prefix[-1] + v)
    half = width // 2
    out = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append(x[i] - (prefix[hi] - prefix[lo]) / (hi - lo))
    return out


def N_velocity(volume: int) -> int:
    return max(1, int(round(volume * 127.0 / 15.0)))


_REF = {}
register_backend(RP2A03Backend())
