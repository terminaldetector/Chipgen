"""ladder.py — one voice pushed through three drivers: NES, Mega Drive, Schism.

A chiptune module is heard as a chiptune because of how its sounds are made,
not which notes they play: a square at 25 % duty has a buzz at the 8th
harmonic, an arpeggio is a chord that cannot afford one, a noise channel is
the only drum. The same melody on a Mega Drive is FM and one DAC channel; on
Impulse Tracker it is whatever a sample can be. Re-forging a voice with the
dials, or mixing archetypes, keeps the buzz: the sound is still a square.
The ladder keeps the *voice* and changes the driver:

    rung 1  nes      {duty, noise_mode, arp_intervals} on one of the four
                     channels. No pad: a pad is an arpeggio there, and the
                     pattern for it is emitted instead of a sound.
    rung 2  sega     a YM2612 voice (algorithm, feedback, four operators in
                     register units, panning) or, for a drum, a DAC one-shot.
                     Played by an IT instrument rendered to PCM at every
                     octave, and kept as operator numbers in the sidecar.
    rung 3  schism   built from the Sega PCM, not the NES cycle: octave zones,
                     a sustain loop on what is held, a one-shot on what is
                     struck, the voice twice at ±6 cents for a stereo sample,
                     a soft clip, a small cabinet, a chorus on the organ, NNA
                     by role, a chord on one key for a stab; and a drum is a
                     stack of its DAC crack over a body.

The lifts are rules, not a model: each is a function from the numbers of one
rung to the numbers of the next, written down below with the reason.
"""

import copy
import json
import math
from dataclasses import dataclass, field
from typing import List, Optional

from . import archetypes, fam_layer, fm
from .dna import DNA
from .family import get_family
from .genome import Genome

RUNGS = ("nes", "sega", "schism")
ROLES = ("lead", "bass", "pad", "organ", "stab", "bell", "snare", "kick",
         "hat")
VOICE_FORMAT = "schism-voice/1"
DUTIES = (0.125, 0.25, 0.5, 0.75)
#: the console's DAC, as a drum player uses it: samples a second, bits
DAC = {"snare": (13300, 8), "kick": (11000, 8), "hat": (16000, 8)}
#: where each role lives on the keyboard
REGISTERS = {"lead": ("A", 4), "bass": ("A", 2), "pad": ("A", 3),
             "organ": ("A", 3), "stab": ("A", 3), "bell": ("A", 4),
             "snare": ("G", 4), "kick": ("A", 1), "hat": ("A", 5)}
#: what an archetype's name says about the role it plays on a console
NAMED = {"organ": "organ", "stab": "stab", "bell": "bell", "glass": "bell",
         "vibes": "bell", "kalimba": "bell", "marimba": "bell",
         "snare": "snare", "clap": "snare", "rim": "snare",
         "kick": "kick", "kick_hard": "kick", "tom": "kick",
         "hat": "hat", "open_hat": "hat", "shaker": "hat", "cymbal": "hat",
         "cowbell": "hat"}
PLAYS = {"bass": "bass", "lead": "lead", "pad": "pad", "harmony": "pad",
         "drums": "snare", "fx": "hat"}


class LadderError(ValueError):
    """A voice the ladder cannot lift, with the reason."""


def role_of(name: str, role: Optional[str] = None) -> str:
    """The role a console would give a sound called `name`."""
    if role:
        if role not in ROLES:
            raise LadderError(f"{role!r} is not a role; the roles are "
                              f"{', '.join(ROLES)}")
        return role
    if name in NAMED:
        return NAMED[name]
    if name in archetypes.ARCHETYPES:
        return PLAYS.get(archetypes.get(name).roles[0], "lead")
    if name in ROLES:
        return name
    return "lead"


# -- rung 1: the NES ------------------------------------------------------------------
@dataclass
class NesVoice:
    name: str
    role: str
    #: pulse (two channels), triangle, noise
    channel: str = "pulse"
    #: pulse: the share of the cycle that is high, 0.125 .. 0.75
    duty: float = 0.25
    #: noise: long (white-ish) or short (the metallic 93-step sequence)
    noise_mode: str = "long"
    #: semitones above the note, stepped through every frame: a chord that
    #: one channel can afford
    arp: List[int] = field(default_factory=lambda: [0])
    #: the volume each 60 Hz frame, 0..15
    envelope: List[int] = field(default_factory=lambda: [15, 13, 12])
    register: tuple = ("A", 4)

    def to_dict(self) -> dict:
        return {"channel": self.channel, "duty": self.duty,
                "noise_mode": self.noise_mode, "arp_intervals": list(self.arp),
                "envelope": list(self.envelope), "role": self.role,
                "register": list(self.register)}

    @classmethod
    def from_dict(cls, name: str, data: dict) -> "NesVoice":
        try:
            return cls(name, data["role"], data.get("channel", "pulse"),
                       float(data.get("duty", 0.25)),
                       data.get("noise_mode", "long"),
                       [int(i) for i in data.get("arp_intervals", [0])],
                       [int(v) for v in data.get("envelope", [15, 13, 12])],
                       tuple(data.get("register", ("A", 4))))
        except (KeyError, TypeError, ValueError) as error:
            raise LadderError(f"a NES voice needs a role and numbers "
                              f"({error})") from None

    def problems(self) -> List[str]:
        out = []
        if self.channel not in ("pulse", "triangle", "noise"):
            out.append(f"channel {self.channel!r}: pulse, triangle or noise")
        if self.channel == "pulse" and self.duty not in DUTIES:
            out.append(f"duty {self.duty}: the NES has "
                       f"{', '.join(str(d) for d in DUTIES)}")
        if self.noise_mode not in ("long", "short"):
            out.append("noise_mode: long or short")
        if not self.arp or any(not -24 <= i <= 24 for i in self.arp):
            out.append("arp_intervals: semitones, within two octaves")
        if any(not 0 <= v <= 15 for v in self.envelope) or not self.envelope:
            out.append("envelope: volumes 0..15, one a frame")
        return out


#: role -> channel, duty, noise mode, arpeggio, volume per frame
_NES = {
    "lead": ("pulse", 0.25, "long", [0], [15, 14, 13, 12]),
    "bass": ("triangle", 0.5, "long", [0], [15]),
    "pad": ("pulse", 0.5, "long", [0, 4, 7], [9, 9, 9, 8]),
    "organ": ("pulse", 0.5, "long", [0], [12]),
    "stab": ("pulse", 0.125, "long", [0, 4, 7], [15, 12, 9, 6, 3, 1]),
    "bell": ("pulse", 0.125, "long", [0, 12], [15, 11, 8, 5, 3, 2, 1]),
    "snare": ("noise", 0.5, "short", [0], [15, 12, 9, 6, 4, 2, 1]),
    "kick": ("noise", 0.5, "long", [0], [15, 13, 10, 7, 4, 2, 1]),
    "hat": ("noise", 0.5, "short", [0], [10, 5, 2, 1]),
}


def nes_voice(name: str, role: Optional[str] = None, duty=None,
              noise_mode=None, arp=None) -> NesVoice:
    """The NES voice a role usually is, with any of it overridden."""
    role = role_of(name, role)
    channel, d, mode, intervals, envelope = _NES[role]
    voice = NesVoice(name, role, channel, d if duty is None else float(duty),
                     mode if noise_mode is None else noise_mode,
                     list(intervals) if arp is None else list(arp),
                     list(envelope), REGISTERS[role])
    bad = voice.problems()
    if bad:
        raise LadderError("; ".join(bad))
    return voice


def nes_line(voice: NesVoice) -> str:
    """The `inst` settings a score uses to play the voice as the NES does."""
    if voice.channel == "pulse":
        # a 75 % pulse is a 25 % pulse upside down: the same spectrum
        duty = min(voice.duty, 1.0 - voice.duty)
        line = f"wave=pulse duty={duty:g} oct=2-6"
    elif voice.channel == "triangle":
        line = "wave=triangle oct=0-4"
    else:
        line = "wave=noise color=white len=0.5"
    frames = len(voice.envelope)
    if frames > 1 or voice.envelope[0] <= 15:
        # one NES frame is 1/60 s and a tick at the default tempo is 1/50 s
        nodes = {}
        for i, v in enumerate(voice.envelope):
            nodes[int(round(i * 0.83))] = int(round(v / 15.0 * 64))
        if voice.envelope[-1] <= 2:
            nodes[max(nodes) + 1] = 0
            marks = {}
        else:
            # held while the key is down, and gone two ticks after it is let
            # go: a NES note ends at once, it does not ring
            marks = {max(nodes): "s"}
            nodes[max(nodes) + 2] = 0
        line += " venv=" + ",".join(f"{t}:{v}{marks.get(t, '')}"
                                    for t, v in sorted(nodes.items()))
    return line + " nna=cut"


def arp_rows(voice: NesVoice, note: str = "C-4", instrument: int = 1,
             rows: int = 8, volume: int = 40) -> List[str]:
    """Rows of a pattern that play the voice's arpeggio the way the NES's
    driver does, one step a tick (`Jxy` steps 0, +x, +y semitones). A chord
    wider than three notes alternates two such rows."""
    steps = [i for i in voice.arp if i]
    if not steps:
        return [f"{note} {instrument:02d} v{volume} ..."] \
            + ["... .. ... ..."] * (rows - 1)
    out = []
    for r in range(rows):
        pair = steps[(r * 2) % len(steps):][:2]
        if len(pair) < 2:
            pair = (pair + steps)[:2]
        x, y = (min(15, p) for p in (pair + pair)[:2])
        first = f"{note} {instrument:02d} v{volume}" if r == 0 else "... .. ..."
        out.append(f"{first} J{x:X}{y:X}")
    return out


# -- rung 2: the Mega Drive -----------------------------------------------------------------
@dataclass
class SegaVoice:
    kind: str                     # "fm" | "dac"
    role: str
    fm: Optional[dict] = None     # an FmPatch
    dac: Optional[dict] = None    # {"drum": kind, "rate": hz, "bits": 8}
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {"kind": self.kind, "role": self.role, "notes": list(self.notes)}
        if self.fm is not None:
            out["fm"] = self.fm
        if self.dac is not None:
            out["dac"] = self.dac
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "SegaVoice":
        try:
            voice = cls(data["kind"], data["role"], data.get("fm"),
                        data.get("dac"), list(data.get("notes", [])))
        except KeyError as error:
            raise LadderError(f"a Sega voice needs {error}") from None
        if voice.kind == "fm":
            if voice.fm is None:
                raise LadderError("an FM voice has no operators")
            try:
                fm.FmPatch.from_dict(voice.fm)
            except fm.FmError as error:
                raise LadderError(str(error)) from None
        elif voice.kind != "dac" or voice.dac is None:
            raise LadderError(f"a Sega voice is fm or dac, not {voice.kind!r}")
        return voice


def _op(**kw) -> dict:
    return fm.Operator(**kw).to_dict()


def _carrier_tl(voice: NesVoice) -> int:
    """The carrier's total level from the loudest the NES voice gets: the
    register is in 0.75 dB steps and the NES volume in sixteenths."""
    loudest = max(voice.envelope) or 1
    return int(round(-20.0 * math.log10(loudest / 15.0) / 0.75))


def lift_to_sega(voice: NesVoice) -> SegaVoice:
    """Each rule is FM as FM: a pulse is not imitated by a pulse. What the
    duty did to the spectrum, an operator does with its level."""
    notes = []
    tl = _carrier_tl(voice)
    if voice.channel == "noise":
        drum = {"snare": "snare", "kick": "kick", "hat": "hat"}.get(
            voice.role, "snare")
        rate, bits = DAC[drum]
        notes.append(f"a noise drum is not an FM voice: it is a DAC one-shot "
                     f"({drum}, {rate} Hz, {bits} bit)")
        return SegaVoice("dac", voice.role, None,
                         {"drum": drum, "rate": rate, "bits": bits}, notes)
    role = voice.role
    if role == "organ":
        patch = fm.FmPatch(7, 0, [
            fm.Operator(mul=1, tl=tl + 4), fm.Operator(mul=2, tl=tl + 13),
            fm.Operator(mul=3, tl=tl + 19, rr=9),
            fm.Operator(mul=6, tl=tl, ar=31, d1r=26, sl=15, rr=12)])
        notes += ["organ: all four operators carry (algorithm 7), drawbar "
                  "multiples 1, 2, 3 (the chip has four operators: of the "
                  "usual 1, 2, 3, 4, 6 the 4 is left out)",
                  "the percussion click is the fourth operator at multiple "
                  "6, attacking at once and gone in a few milliseconds"]
    elif role == "bass":
        patch = fm.FmPatch(0, 3, [
            fm.Operator(mul=1, tl=50, ar=31, d1r=15, sl=9, rr=9),
            fm.Operator(mul=1, tl=40, ar=31, d1r=14, sl=7, rr=9),
            fm.Operator(mul=2, tl=46, ar=31, d1r=16, sl=9, rr=9),
            fm.Operator(mul=1, tl=tl, ar=31, rr=9)])
        notes += ["triangle bass: algorithm 0, a chain of low multiples (1, 1, "
                  "2) into a carrier at 1",
                  "short decays on the modulators: they bite at the start and "
                  "leave the carrier clean"]
    elif role == "pad":
        patch = fm.FmPatch(4, 2, [
            fm.Operator(mul=1, tl=46, ar=15, d1r=8, sl=6, rr=5),
            fm.Operator(mul=1, dt=2, tl=tl + 6, ar=15, rr=5),
            fm.Operator(mul=2, tl=52, ar=16, d1r=8, sl=5, rr=5),
            fm.Operator(mul=1, dt=-2, tl=tl + 6, ar=15, rr=5)])
        notes += ["a pad is an arpeggio on the NES (see `arp_rows`); here it "
                  "is one voice: two carriers detuned a step either way, a "
                  "slow attack"]
    elif role == "stab":
        patch = fm.FmPatch(5, 3, [
            fm.Operator(mul=1, tl=30, ar=31, d1r=17, sl=15, rr=12),
            fm.Operator(mul=1, dt=2, tl=tl + 2, ar=31, d1r=16, sl=15, rr=12),
            fm.Operator(mul=2, tl=tl + 8, ar=31, d1r=17, sl=15, rr=12),
            fm.Operator(mul=1, dt=-2, tl=tl + 4, ar=31, d1r=16, sl=15, rr=12)])
        notes += ["a stab is a short pulse chord: algorithm 5, one modulator "
                  "into three carriers, every envelope falling to nothing"]
    elif role == "bell":
        patch = fm.FmPatch(4, 0, [
            fm.Operator(mul=7, tl=34, ar=31, d1r=12, sl=15, rr=8),
            fm.Operator(mul=1, tl=tl + 2, ar=31, d1r=11, sl=15, rr=8),
            fm.Operator(mul=3, tl=40, ar=31, d1r=13, sl=15, rr=8),
            fm.Operator(mul=1, dt=1, tl=tl + 8, ar=31, d1r=9, sl=15, rr=8)])
        notes += ["a bell is two pairs, a high multiple (7) and 3 into two "
                  "carriers, falling to nothing"]
    else:                                         # lead
        # what a narrow duty does to the spectrum, a modulator does with its
        # level: the narrower, the brighter
        mod = {0.125: 24, 0.25: 32, 0.5: 40, 0.75: 32}[voice.duty]
        bright = voice.duty == 0.125
        patch = fm.FmPatch(5 if bright else 4, 6 if bright else 5, [
            fm.Operator(mul=1, tl=mod, ar=31, d1r=12, sl=5, rr=8),
            fm.Operator(mul=1, dt=3, tl=tl, ar=31, rr=8),
            fm.Operator(mul=2, tl=mod + (-12 if bright else 6), ar=31,
                        d1r=14, sl=7, rr=8) if not bright else
            fm.Operator(mul=2, tl=tl + 8, ar=31, rr=8),
            fm.Operator(mul=1, dt=-3, tl=tl, ar=31, rr=8)])
        notes += [f"pulse lead at duty {voice.duty:g}: "
                  f"algorithm {patch.algorithm} "
                  f"({'one modulator into three carriers' if bright else 'two pairs'}), "
                  f"feedback {patch.feedback} on operator 1, a detune of +3 "
                  f"and -3 on the carrier pair",
                  "the duty's brightness is the modulator's level, TL "
                  f"{mod}; its short decay is the pulse's bite"]
    patch.pan = "LR"
    notes.append("no stereo sample on the console: panning is the voice's "
                 "register (LR)")
    return SegaVoice("fm", role, patch.to_dict(), None, notes)


def sega_genome(voice: SegaVoice, name: str) -> Genome:
    """The genome that compiles to the Sega rung of a voice."""
    if voice.kind == "fm":
        return Genome("fm", DNA(), style={"fm": voice.fm, "nna": "cut",
                                          "role": voice.role},
                      name=name, register=REGISTERS[voice.role],
                      lineage=[f"ladder:sega:{voice.role}"])
    drum = voice.dac["drum"]
    arch = archetypes.get(drum)
    style = dict(arch.style)
    style["dac"] = {"rate": voice.dac["rate"], "bits": voice.dac["bits"]}
    return Genome("drum", arch.dna, style=style, name=name,
                  register=arch.register,
                  lineage=[f"ladder:sega:{voice.role}"])


# -- rung 3: Schism ---------------------------------------------------------------------------
#: role -> what the Schism rung adds to the Sega voice
SCHISM = {
    "lead": {"stereo": 6.0, "post": {"drive": 1.6, "cabinet": True},
             "nna": "cut"},
    "bass": {"stereo": 0.0, "post": {"drive": 1.4, "cabinet": 35},
             "nna": "cut"},
    "pad": {"stereo": 6.0, "post": {"drive": 1.2, "cabinet": True},
            "nna": "fade"},
    "organ": {"stereo": 6.0, "post": {"drive": 0.8, "cabinet": True,
                                      "chorus": {"swings": 1,
                                                 "depth_ms": 1.6,
                                                 "centre_ms": 7.0,
                                                 "mix": 0.5}},
              "nna": "fade"},
    "stab": {"stereo": 6.0, "post": {"drive": 1.6, "cabinet": True},
             "nna": "cut", "chord": "min7"},
    "bell": {"stereo": 6.0, "post": {"cabinet": True}, "nna": "cont"},
}


def lift_to_schism(voice: SegaVoice, name: str, bodies=None):
    """-> (Genome, Compiled, [notes]) for the Schism rung, from the Sega
    voice. A drum is a stack, so `bodies` (a function kind -> Source that
    gives the body of a drum) must be given for one."""
    notes = ["built from the Sega voice rendered to PCM at every octave, not "
             "from the NES cycle"]
    if voice.kind == "fm":
        spec = copy.deepcopy(SCHISM[voice.role])
        style = {"fm": voice.fm, "nna": spec["nna"], "role": voice.role,
                 "post": spec["post"]}
        if spec.get("stereo"):
            style["stereo"] = spec["stereo"]
        if spec.get("chord"):
            style["chord"] = {"notes": spec["chord"], "root": 0}
        genome = Genome("fm", DNA(), style=style, name=name,
                        register=REGISTERS[voice.role],
                        lineage=[f"ladder:schism:{voice.role}"])
        compiled = get_family("fm").compile(genome)
        held = compiled.style["held"]
        notes.append(("a sustain loop: the voice is held" if held
                      else "a one-shot: the voice is struck"))
        if spec.get("stereo"):
            notes.append(f"stereo: the voice twice, {spec['stereo']:g} cents "
                         f"flat and sharp")
        post = spec["post"]
        stack = ["drive " + str(post["drive"])] if post.get("drive") else []
        if post.get("cabinet"):
            stack.append("a small cabinet")
        if post.get("chorus"):
            stack.append("a chorus (organ only)")
        if stack:
            notes.append("post: " + ", then ".join(stack))
        if spec.get("chord"):
            notes.append(f"a chord on one key: {spec['chord']}")
        notes.append(f"NNA {spec['nna']}")
        return genome, compiled, notes
    if bodies is None:
        raise LadderError("a drum's Schism rung needs a body to put its DAC "
                          "crack over")
    kind = voice.dac["drum"]
    genome = sega_genome(voice, name + "_dac")
    dac = get_family("drum").compile(genome)
    dac.name = name + "_dac"
    body = bodies(kind)
    roles = {"snare": ("noise", {"hp": 1500.0, "ms": 250.0}),
             "kick": ("click", {"ms": 30.0}),
             "hat": ("noise", {"hp": 4000.0, "ms": 150.0})}[kind]
    sources = [fam_layer.Source(name + "_dac", dac, genome, roles[0],
                                roles[1]), body]
    stack_genome, stack = fam_layer.build_stack(sources, name)
    stack.patch.inst["nna"] = "cut"
    notes.append(f"a {kind} is two things: the DAC one-shot ({voice.dac['rate']} "
                 f"Hz, {voice.dac['bits']} bit) as the {roles[0]} and a "
                 f"tuned body under it")
    notes.append("NNA cut")
    return stack_genome, stack, notes


# -- the file ------------------------------------------------------------------------------------
@dataclass
class Voice:
    name: str
    role: str
    nes: NesVoice
    sega: Optional[SegaVoice] = None
    #: what the Schism rung is called in a bank
    schism: Optional[dict] = None

    def to_dict(self) -> dict:
        data = {"format": VOICE_FORMAT, "name": self.name, "role": self.role,
                "nes": self.nes.to_dict()}
        if self.sega is not None:
            data["sega"] = self.sega.to_dict()
        if self.schism is not None:
            data["schism"] = self.schism
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Voice":
        if data.get("format") != VOICE_FORMAT:
            raise LadderError(f"not a voice file (format "
                              f"{data.get('format')!r}, want "
                              f"{VOICE_FORMAT!r})")
        name = data["name"]
        return cls(name, data["role"],
                   NesVoice.from_dict(name, data["nes"]),
                   SegaVoice.from_dict(data["sega"]) if data.get("sega")
                   else None, data.get("schism"))

    def save(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=1)
            handle.write("\n")
        return path

    @classmethod
    def load(cls, path: str) -> "Voice":
        try:
            with open(path, encoding="utf-8") as handle:
                return cls.from_dict(json.load(handle))
        except FileNotFoundError:
            raise LadderError(f"no voice file at {path}") from None
        except json.JSONDecodeError as error:
            raise LadderError(f"{path} is not JSON: {error}") from None


def operator_table(patch: fm.FmPatch) -> str:
    """The voice the way a patch dump on a console lists it."""
    lines = [f"ALG {patch.algorithm}  FB {patch.feedback}  PAN {patch.pan}   "
             f"{fm.ALGORITHM_DOC[patch.algorithm]}",
             "op  MUL  DT  TL  KS  AR D1R  SL D2R  RR"]
    carriers = patch.carriers()
    for n, op in enumerate(patch.ops, 1):
        lines.append(f" {n}  {op.mul:3d} {op.dt:+3d} {op.tl:3d} {op.ks:3d} "
                     f"{op.ar:3d} {op.d1r:3d} {op.sl:3d} {op.d2r:3d} "
                     f"{op.rr:3d}   {'carrier' if n - 1 in carriers else ''}")
    return "\n".join(lines)


# -- the same notes on each rung -------------------------------------------------------------
#: role -> [(row, chord/notes, rows held)] in a 32-row pattern
_RIFF = {
    "lead": [(0, ["C-5"], 3), (4, ["E-5"], 3), (8, ["G-5"], 3), (12, ["E-5"], 3),
             (16, ["D-5"], 3), (20, ["F-5"], 3), (24, ["A-5"], 3),
             (28, ["G-5"], 3)],
    "bass": [(0, ["C-2"], 6), (8, ["C-2"], 6), (16, ["G-2"], 6),
             (24, ["F-2"], 6)],
    "organ": [(0, ["C-4", "E-4", "G-4"], 14), (16, ["F-4", "A-4", "C-5"], 14)],
    "pad": [(0, ["C-4", "E-4", "G-4"], 15), (16, ["F-4", "A-4", "C-5"], 15)],
    "stab": [(0, ["C-4", "E-4", "G-4"], 2), (8, ["C-4", "E-4", "G-4"], 2),
             (12, ["F-4", "A-4", "C-5"], 2), (20, ["C-4", "E-4", "G-4"], 2),
             (24, ["F-4", "A-4", "C-5"], 2)],
    "bell": [(0, ["C-5"], 1), (2, ["E-5"], 1), (4, ["G-5"], 1), (6, ["C-6"], 1),
             (8, ["G-5"], 1), (10, ["E-5"], 1), (12, ["C-5"], 1),
             (16, ["D-5"], 1), (18, ["F-5"], 1), (20, ["A-5"], 1),
             (22, ["D-6"], 1), (24, ["A-5"], 1), (26, ["F-5"], 1),
             (28, ["D-5"], 1)],
    # a drum is played at the pitch it is tuned to ("REG")
    "snare": [(r, ["REG"], 0) for r in (4, 12, 20, 28)],
    "kick": [(r, ["REG"], 0) for r in (0, 6, 8, 16, 22, 24)],
    "hat": [(r, ["REG"], 0) for r in range(0, 32, 2)],
}


def _register_note(role: str) -> str:
    name, octave = REGISTERS[role]
    return name + ("-" if len(name) == 1 else "") + str(octave)


def _section(role: str, instrument: int, channels: int, arpeggio: Optional[NesVoice]):
    """The 32 rows of a pattern, `channels` wide, for one instrument."""
    rows = [["... .. ... ..."] * channels for _ in range(32)]
    for row, notes, held in _RIFF[role]:
        notes = [_register_note(role) if n == "REG" else n for n in notes]
        if arpeggio is not None and len(notes) > 1:
            # the NES cannot play a chord: one channel steps through it
            ar = arp_rows(arpeggio, notes[0], instrument, held + 1, 40)
            for k, text in enumerate(ar):
                if row + k < 32:
                    rows[row + k][0] = text
        else:
            for ch, note in enumerate(notes[:channels]):
                rows[row][ch] = f"{note} {instrument:02d} v64 ..."
        end = row + held + 1
        if held and end < 32:
            for ch in range(min(channels, len(notes) if arpeggio is None
                                else 1)):
                if rows[end][ch].startswith("..."):
                    rows[end][ch] = "=== .. ... ..."
    return [" | ".join(r) for r in rows]


def demo_score(voice: Voice, bank_file: str, sega_name: Optional[str],
               schism_name: Optional[str]) -> str:
    """A score that plays one note list on each rung that has been lifted,
    one after the other: the NES's instrument line, the Sega voice, the
    Schism instrument."""
    role = voice.role
    chordal = len(_RIFF[role][0][1]) > 1
    lines = [f"title {voice.name} up the ladder", "tempo 125", "channels 3",
             "mv 48"]
    if sega_name or schism_name:
        lines.append(f"bank {bank_file}")
    lines.append(f"inst 1 name=NES {nes_line(voice.nes)}")
    sections = [(1, voice.nes if chordal else None)]
    if sega_name:
        lines.append(f"inst 2 name=Sega forge={sega_name}")
        sections.append((2, None))
    if schism_name:
        lines.append(f"inst 3 name=Schism forge={schism_name}")
        sections.append((3, None))
    order = []
    for index, (instrument, arpeggio) in enumerate(sections):
        label = "abc"[index]
        lines.append(f"pattern {label} rows 32")
        lines.extend(_section(role, instrument, 3, arpeggio))
        lines.append("end")
        order.append(label)
    lines.append("order " + " ".join(order))
    return "\n".join(lines) + "\n"


def make_entry(name: str, genome: Genome, compiled, role: str,
               character: str):
    """A bank entry for a rung: played, levelled to the reference loudness
    and measured, if libopenmpt is there; stored as it is, and said so, if
    not."""
    from . import evolve, probe, score as score_mod
    from .bank import Entry
    compiled.name = name
    genome = genome.clone(name=name)
    issues = []
    try:
        result = probe.probe(compiled, genome, full=True)
        evolve.auto_level(get_family(compiled.family), compiled, result)
        result = probe.probe(compiled, genome, full=True)
        measured = dict(result.axes)
        issues = [str(i) for i in result.issues]
        quality = score_mod.quality(result.issues)
    except probe.ProbeUnavailable:
        measured, quality = {}, 1.0
        issues.append("not measured or levelled: libopenmpt is not "
                      "installed")
    return Entry(name, compiled.family, genome, compiled, measured, quality,
                 role, character, issues)
