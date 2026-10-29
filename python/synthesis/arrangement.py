"""arrangement.py — where an instrument can sit, and what it does to its neighbours.

A patch that measures well alone can still ruin a track: a pad that blankets
the lead's midrange, two basses on one octave, a bell whose partials land
a semitone from the melody, a lead that is quieter than the arpeggio under
it. None of that is visible in one instrument's dials. This module looks at
parts together:

* a **part profile** — the notes of one voice: register, density, how long
  its notes are held against the gaps, how hard it is played, the roles
  roles.py gives it;
* an **instrument profile** — what a probe of the instrument measured at
  the part's register: its partials, attack, fall, sustain, release, loudness;
* a **spectral footprint** — the two put together: for every row of the
  score, how much energy the part puts in each of 24 Bark bands, using the
  instrument's own envelope and partials. No audio is rendered for this;
  one probe per instrument is the whole cost;
* the **conflicts** between footprints, named: masking, a bass stacked on a
  bass, a sustained part blanketing a busy one, onsets that land together,
  notes a semitone apart in two metallic voices, a part that drowns the
  lead, a part that needs channels another part is using.

`suggest` turns conflicts into changes in DNA (and level and register);
`fit` carries one out, with real renders, and says whether it helped.
"""

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import score_model
import roles as roles_mod

from . import evolve, probe as probe_mod
from .backends import get_backend
from .backends.base import Compiled
from .dna import AXES, DNA
from .genome import Genome

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
BANDS = 24
#: rough equal-loudness weighting per Bark band (the ear's low and very high
#: ends count for less)
BAND_WEIGHT = (0.35, 0.5, 0.65, 0.8, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
               1.0, 1.0, 0.95, 0.9, 0.85, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2)
#: how unpleasant two notes are at each interval (semitones mod 12)
DISSONANCE = (0.0, 1.0, 0.5, 0.1, 0.05, 0.15, 0.8, 0.02, 0.1, 0.05, 0.45, 0.9)
#: how loud each role wants to be against the lead, dB
ROLE_LEVEL_DB = {"lead": 0.0, "counter": -4.0, "harmony": -4.0, "arp": -6.0,
                 "pad": -7.0, "bass": -2.0, "drums": -3.0}
CHANNELS = {"ym2612": 6, "opl2": 9, "nes": 4}
#: a part whose median note is under this (D3) and whose role is a guess is
#: treated as the bass
BASS_BELOW = 50


def hz_of(pitch: int) -> float:
    return 440.0 * 2.0 ** ((pitch - 57) / 12.0)


def bark(f: float) -> float:
    return 13.0 * math.atan(0.00076 * f) + 3.5 * math.atan((f / 7500.0) ** 2)


def band_of(f: float) -> int:
    return max(0, min(BANDS - 1, int(bark(f))))


def band_edge_hz(band: int) -> float:
    """The frequency where Bark band `band` begins (so band 3 begins where
    bark() reaches 3)."""
    lo, hi = 0.0, 20000.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if bark(mid) < band:
            lo = mid
        else:
            hi = mid
    return hi


#: the bands a bass owns (everything under ~300 Hz) and the band count used
#: for the message that says so
LOW_BANDS = 3
LOW_HZ = band_edge_hz(LOW_BANDS)


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------
@dataclass
class PartProfile:
    voice: str
    notes: int = 0
    low: int = 0
    high: int = 0
    median: float = 0.0
    density: float = 0.0           # notes per bar
    mean_length: float = 0.0       # rows
    legato: float = 0.0            # share of the part's span that is sounding
    velocity: float = 100.0
    role: str = ""
    role_confidence: float = 0.0
    onsets: frozenset = frozenset()

    @classmethod
    def of(cls, voice, notes, score) -> "PartProfile":
        if not notes:
            return cls(voice)
        pitches = sorted(n.pitch for n in notes)
        span = max(1, score.rows)
        sounding = sum(n.length for n in notes)
        bars = max(1.0, score.rows / score.bar)
        ranked = roles_mod.classify(voice, score, notes) \
            if len(notes) >= roles_mod.MIN_NOTES else {}
        role, conf = (next(iter(ranked.items())) if ranked else ("", 0.0))
        return cls(voice, len(notes), pitches[0], pitches[-1],
                   float(pitches[len(pitches) // 2]), len(notes) / bars,
                   sounding / len(notes), min(1.0, sounding / span),
                   sum(_velocity_of(voice, n.velocity) for n in notes)
                   / len(notes), role, conf, frozenset(n.row for n in notes))


def _velocity_of(voice: str, velocity: int) -> float:
    """On the 1-127 scale, whatever the voice's own scale is."""
    if voice.startswith("psg"):                  # 0 is loudest, 15 silent
        return max(1.0, (15 - velocity) / 15.0 * 127.0)
    return float(velocity)


@dataclass
class InstrumentProfile:
    name: str
    backend: str
    axes: Dict[str, float]
    harmonics: List[float]
    attack_s: float
    decay_s: Optional[float]
    sustain: float
    release_s: float
    level: float                    # window RMS against the chip's reference
    f0: float
    voices: int = 1
    lfo: Optional[int] = None


def profile_instrument(backend, compiled: Compiled, pitch: int,
                       genome: Optional[Genome] = None) -> InstrumentProfile:
    octave, index = divmod(pitch, 12)
    note = NOTE_NAMES[index]
    g = genome or Genome(backend.name, DNA(), name=compiled.name,
                         register=(note, octave))
    result = probe_mod.probe(backend, compiled, g, note=note, octave=octave,
                             full=False)
    m = result.measurement
    ref = backend.reference_level() or 1.0
    voices = backend.voices_for(compiled) if hasattr(backend, "voices_for") \
        else 1
    return InstrumentProfile(
        compiled.name, backend.name,
        {k: v for k, v in m.axes.items() if v is not None},
        list(m.harmonics) or [1.0], m.raw.get("attack_s", 0.0),
        m.raw.get("decay_s"), m.raw.get("sustain_ratio", 1.0),
        m.raw.get("release_s", 0.1), result.level / ref, hz_of(pitch),
        voices, compiled.setup.get("lfo") if compiled.setup else None)


def envelope_at(p: InstrumentProfile, t: float, length_s: float) -> float:
    """Amplitude (0..1) of a note t seconds after it began, key down for
    `length_s` seconds, from the instrument's measured envelope."""
    def held(x):
        a = min(1.0, x / p.attack_s) if p.attack_s > 1e-4 else 1.0
        if p.decay_s:
            tau = max(1e-3, p.decay_s)
            fall = p.sustain + (1.0 - p.sustain) * math.exp(-x / tau)
        else:
            fall = 1.0
        return a * fall
    if t <= length_s:
        return held(t)
    tau_r = max(1e-3, p.release_s / math.log(10.0))
    return held(length_s) * math.exp(-(t - length_s) / tau_r)


# --------------------------------------------------------------------------
# Footprints
# --------------------------------------------------------------------------
def footprint(part_notes, p: InstrumentProfile, score, scale: float = 1.0):
    """{row: [band energies]} for one voice with one instrument."""
    row_s = 60.0 / (score.bpm * score.lpb)
    out: Dict[int, List[float]] = {}
    gain = scale * p.level
    for n in part_notes:
        length_s = n.length * row_s
        vel = (_velocity_of(p.name, n.velocity) / 127.0) ** 1.0
        amps = _partials(p, n.pitch)
        rows = n.length + int(math.ceil(p.release_s / row_s)) + 0
        for r in range(n.row, min(score.rows, n.row + rows)):
            t = (r - n.row + 0.5) * row_s
            e = envelope_at(p, t, length_s)
            if e <= 1e-4:
                continue
            slot = out.setdefault(r, [0.0] * BANDS)
            weight = (gain * vel * e) ** 2
            for band, a2 in amps.items():
                slot[band] += weight * a2
    return out


def _partials(p: InstrumentProfile, pitch: int) -> Dict[int, float]:
    """{band: summed power of the partials in it} for a note at `pitch`,
    from the instrument's measured harmonic profile."""
    f0 = hz_of(pitch)
    out: Dict[int, float] = {}
    for k, a in enumerate(p.harmonics, 1):
        f = k * f0
        if f > 16000.0:
            break
        out[band_of(f)] = out.get(band_of(f), 0.0) + a * a
    return out


def total(fp) -> float:
    return sum(sum(v) for v in fp.values())


def overlap(a, b) -> float:
    """Cosine similarity of two footprints in time x band, weighted by how
    audible each band is: 0 = they never share a band at a time, 1 = the
    same."""
    num = 0.0
    for r, va in a.items():
        vb = b.get(r)
        if vb is None:
            continue
        for i in range(BANDS):
            num += math.sqrt(va[i] * vb[i]) * BAND_WEIGHT[i]
    ta, tb = total(a), total(b)
    if ta <= 0 or tb <= 0:
        return 0.0
    return min(1.0, num / math.sqrt(ta * tb))


# --------------------------------------------------------------------------
# Conflicts
# --------------------------------------------------------------------------
@dataclass
class Conflict:
    kind: str
    parts: tuple
    severity: float                  # 0..1
    message: str
    where: str = ""
    data: dict = field(default_factory=dict)   # numbers a suggestion needs

    def __str__(self):
        loc = f" ({self.where})" if self.where else ""
        return (f"[{self.severity:.2f}] {self.kind}: {self.message}{loc}")


@dataclass
class Report:
    conflicts: List[Conflict] = field(default_factory=list)
    parts: Dict[str, PartProfile] = field(default_factory=dict)
    instruments: Dict[str, InstrumentProfile] = field(default_factory=dict)
    levels_db: Dict[str, float] = field(default_factory=dict)

    @property
    def worst(self) -> float:
        return max((c.severity for c in self.conflicts), default=0.0)

    @property
    def cost(self) -> float:
        """One number to bring down: the severities, summed."""
        return sum(c.severity for c in self.conflicts)

    def text(self) -> str:
        lines = []
        for v, p in self.parts.items():
            i = self.instruments.get(v)
            lines.append(
                f"  {v:8s} {p.notes:4d} notes  "
                f"{_name_of(p.low)}-{_name_of(p.high)}  "
                f"{p.density:4.1f}/bar  legato {p.legato:.2f}  "
                f"role {p.role or '?'}"
                + (f"  {i.name} ({i.backend}) "
                   f"{self.levels_db.get(v, 0):+.1f} dB" if i else ""))
        if self.conflicts:
            lines.append("conflicts, worst first:")
            lines += ["  " + str(c) for c in
                      sorted(self.conflicts, key=lambda c: -c.severity)]
        else:
            lines.append("no conflicts found")
        return "\n".join(lines)


def _name_of(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12}"


class Arrangement:
    """A score, an instrument per voice, and the analysis of how they sit."""

    def __init__(self, score, assignment: Dict[str, tuple],
                 scales: Optional[Dict[str, float]] = None,
                 roles: Optional[Dict[str, str]] = None):
        """`assignment`: {voice: (backend name, Compiled)}. `scales`
        multiplies a voice's level (velocity scaling). `roles` names a
        voice's job (bass, lead, harmony, arp, pad, counter, drums) when
        the score's own shape does not say it clearly enough."""
        self.score = score
        self.assignment = dict(assignment)
        self.scales = dict(scales or {})
        self.roles = dict(roles or {})
        self._profiles: Dict[str, InstrumentProfile] = {}
        self._parts: Dict[str, PartProfile] = {}
        self._fps: Dict[str, dict] = {}

    # -- building -------------------------------------------------------------
    def part(self, voice) -> PartProfile:
        if voice not in self._parts:
            p = PartProfile.of(voice, self.score.voices.get(voice, []),
                               self.score)
            if voice in self.roles:
                p.role, p.role_confidence = self.roles[voice], 1.0
            elif p.notes >= 4 and p.median < BASS_BELOW \
                    and p.role_confidence < 0.45:
                # the shape-based guess is soft; a part that low and that
                # unsure is the bass
                p.role, p.role_confidence = "bass", 0.5
            self._parts[voice] = p
        return self._parts[voice]

    def instrument(self, voice) -> InstrumentProfile:
        if voice not in self._profiles:
            backend_name, compiled = self.assignment[voice]
            backend = get_backend(backend_name)
            part = self.part(voice)
            pitch = int(round(part.median)) if part.notes else 57
            self._profiles[voice] = profile_instrument(backend, compiled,
                                                       pitch)
            self._profiles[voice].name = compiled.name
        return self._profiles[voice]

    def footprint(self, voice):
        if voice not in self._fps:
            self._fps[voice] = footprint(self.score.voices.get(voice, []),
                                         self.instrument(voice), self.score,
                                         self.scales.get(voice, 1.0))
        return self._fps[voice]

    def invalidate(self, voice=None):
        for cache in (self._profiles, self._fps):
            if voice is None:
                cache.clear()
            else:
                cache.pop(voice, None)

    # -- analysis ---------------------------------------------------------------
    def analyse(self) -> Report:
        voices = [v for v in self.assignment if self.part(v).notes]
        report = Report()
        for v in voices:
            report.parts[v] = self.part(v)
            report.instruments[v] = self.instrument(v)
        fps = {v: self.footprint(v) for v in voices}
        # loudness of each part as a listener balances it: the loudest
        # sliding stretch of its footprint, in dB re the quietest part
        loud = {v: _loudness(fps[v]) for v in voices}
        ref = max(loud.values(), default=1.0) or 1.0
        report.levels_db = {v: 10.0 * math.log10(max(loud[v], 1e-12) / ref)
                            for v in voices}
        self._masking(report, voices, fps)
        self._bass(report, voices, fps)
        self._blanket(report, voices)
        self._onsets(report, voices)
        self._clash(report, voices)
        self._dynamics(report, voices, loud)
        self._channels(report, voices)
        return report

    def _masking(self, report, voices, fps):
        for i, a in enumerate(voices):
            for b in voices[i + 1:]:
                o = overlap(fps[a], fps[b])
                if o < 0.55:
                    continue
                # how lopsided: the louder part masks the quieter one
                la, lb = _loudness(fps[a]), _loudness(fps[b])
                gap = abs(10 * math.log10(max(la, 1e-12) / max(lb, 1e-12)))
                quiet = b if la > lb else a
                severity = min(1.0, (o - 0.5) * 2.0) * min(1.0, 0.4 + gap / 12)
                if severity < 0.15:
                    continue
                report.conflicts.append(Conflict(
                    "masking", (a, b), severity,
                    f"{a} and {b} share {o:.0%} of their spectrum at the "
                    f"same times; {quiet} is {gap:.0f} dB under and is the "
                    f"one that disappears"))

    def _bass(self, report, voices, fps):
        lows = [v for v in voices if self.part(v).median < 50
                and self.part(v).notes >= 4]
        if len(lows) >= 2:
            a, b = sorted(lows, key=lambda v: self.part(v).median)[:2]
            keep = LOW_BANDS + 2
            o = overlap({r: s[:keep] + [0.0] * (BANDS - keep)
                         for r, s in fps[a].items()},
                        {r: s[:keep] + [0.0] * (BANDS - keep)
                         for r, s in fps[b].items()})
            if o > 0.4:
                report.conflicts.append(Conflict(
                    "bass_stack", (a, b), min(1.0, o),
                    f"{a} and {b} both live under {_name_of(50)} and share "
                    f"their low bands ({o:.0%}): the bottom turns to mud"))
        if lows:
            bass = min(lows, key=lambda v: self.part(v).median)
            bass_low = sum(sum(s[:LOW_BANDS]) for s in fps[bass].values())
            for v in voices:
                if v == bass:
                    continue
                other = sum(sum(s[:LOW_BANDS]) for s in fps[v].values())
                if bass_low > 0 and other > 0.35 * bass_low \
                        and self.part(v).median >= 50:
                    report.conflicts.append(Conflict(
                        "mud", (bass, v), min(1.0, other / bass_low),
                        f"{v} puts {other / bass_low:.0%} as much energy "
                        f"under {LOW_HZ:.0f} Hz as the bass does, from a "
                        f"register that should not be there",
                        ))

    def _blanket(self, report, voices):
        """A part that holds long notes over a part that moves."""
        for a in voices:
            pa = self.part(a)
            ia = self.instrument(a)
            if pa.legato < 0.75 or ia.sustain < 0.5:
                continue
            for b in voices:
                if a == b:
                    continue
                pb = self.part(b)
                if pb.density <= 1.6 * max(pa.density, 0.5):
                    continue
                if abs(pa.median - pb.median) > 14:
                    continue
                report.conflicts.append(Conflict(
                    "sustain_blanket", (a, b),
                    min(1.0, 0.35 + 0.5 * pa.legato * ia.sustain),
                    f"{a} holds {pa.legato:.0%} of the time at sustain "
                    f"{ia.sustain:.0%} and sits within an octave of {b}, "
                    f"which plays {pb.density:.0f} notes a bar: "
                    f"{a} fills the gaps {b} needs"))

    def _onsets(self, report, voices):
        for i, a in enumerate(voices):
            for b in voices[i + 1:]:
                pa, pb = self.part(a), self.part(b)
                if not pa.onsets or not pb.onsets:
                    continue
                same = len(pa.onsets & pb.onsets) / min(len(pa.onsets),
                                                        len(pb.onsets))
                sharp = (self.instrument(a).attack_s < 0.012
                         and self.instrument(b).attack_s < 0.012)
                if same >= 0.7 and sharp and pa.notes >= 6:
                    report.conflicts.append(Conflict(
                        "onsets", (a, b), min(1.0, (same - 0.5)),
                        f"{same:.0%} of the notes of {a} and {b} start on "
                        f"the same row and both attack in under 12 ms: the "
                        f"hits stack into one"))

    def _clash(self, report, voices):
        for i, a in enumerate(voices):
            for b in voices[i + 1:]:
                worst, at, count = 0.0, None, 0
                na, nb = self.score.voices[a], self.score.voices[b]
                if not na or not nb:
                    continue
                grain = (1.0 + self.instrument(a).axes.get("metallicity", 0)
                         + self.instrument(b).axes.get("metallicity", 0))
                events = 0
                total_w = 0.0
                for x in na:
                    for y in nb:
                        if y.row >= x.end:
                            break
                        if y.end <= x.row:
                            continue
                        events += 1
                        span = min(x.end, y.end) - max(x.row, y.row)
                        d = DISSONANCE[abs(x.pitch - y.pitch) % 12]
                        if abs(x.pitch - y.pitch) < 12 and d > 0.4:
                            total_w += d * span
                            count += 1
                            if d * grain > worst:
                                worst, at = d * grain, max(x.row, y.row)
                if events and total_w / max(1, events) > 0.12:
                    sev = min(1.0, (total_w / max(1, events)) * grain)
                    if sev >= 0.2:
                        report.conflicts.append(Conflict(
                            "clash", (a, b), sev,
                            f"{count} stretches where {a} and {b} sound "
                            f"notes a second, tritone or seventh apart"
                            + (" (both timbres are metallic, which "
                               "sharpens it)" if grain > 1.6 else ""),
                            f"first near row {at}" if at is not None else ""))

    def _dynamics(self, report, voices, loud):
        by_role = {}
        for v in voices:
            p = self.part(v)
            by_role[v] = p.role or ("bass" if p.median < BASS_BELOW
                                    else "harmony")
        leads = [v for v in voices if by_role[v] == "lead"]
        if not leads:
            return
        lead = max(leads, key=lambda v: loud[v])
        for v in voices:
            if v == lead:
                continue
            want = ROLE_LEVEL_DB.get(by_role[v], -4.0)
            have = 10 * math.log10(max(loud[v], 1e-12) / max(loud[lead],
                                                             1e-12))
            if have > want + 3.0:
                report.conflicts.append(Conflict(
                    "dynamics", (lead, v), min(1.0, (have - want) / 12.0),
                    f"{v} ({by_role[v]}) is {have:+.1f} dB against the lead "
                    f"{lead}; a {by_role[v]} belongs near {want:+.0f} dB",
                    data={"have_db": have, "want_db": want}))

    def _channels(self, report, voices):
        playing = {self.assignment[v][0] + ":" + v: v for v in voices}
        taken: Dict[str, str] = {}
        for v in voices:
            backend, compiled = self.assignment[v]
            profile = self.instrument(v)
            for slot in _slots(backend, v, profile.voices)[1:]:
                key = backend + ":" + slot
                if key in playing and playing[key] != v:
                    report.conflicts.append(Conflict(
                        "channel", (v, playing[key]), 0.9,
                        f"{v}'s detune layer needs {slot}, which is playing "
                        f"its own part; the layer would be dropped "
                        f"(an instrument with {profile.voices} voices per "
                        f"note takes that many channels in a row)"))
                elif key in taken and taken[key] != v:
                    report.conflicts.append(Conflict(
                        "channel", (v, taken[key]), 0.9,
                        f"the detune layers of {v} and {taken[key]} both "
                        f"need {slot}"))
                else:
                    taken[key] = v
        lfos = {v: self.instrument(v).lfo for v in voices
                if self.assignment[v][0] == "ym2612"
                and self.instrument(v).lfo is not None}
        if len(set(lfos.values())) > 1:
            by_rate: Dict[int, list] = {}
            for v, r in lfos.items():
                by_rate.setdefault(r, []).append(v)
            commonest = max(by_rate, key=lambda r: len(by_rate[r]))
            odd = [v for v, r in lfos.items() if r != commonest]
            report.conflicts.append(Conflict(
                "lfo", tuple(sorted(lfos)[:2]), 0.5,
                "the YM2612 has one LFO for all six channels but "
                + " and ".join(f"rate {r} ({', '.join(vs)})"
                               for r, vs in sorted(by_rate.items())),
                data={"rate": commonest, "parts": odd}))

    # -- suggestions -----------------------------------------------------------------
    def suggest(self, report: Optional[Report] = None) -> List[dict]:
        report = report or self.analyse()
        out, seen = [], set()
        for c in sorted(report.conflicts, key=lambda c: -c.severity):
            s = self._suggestion(c, report)
            if not s:
                continue
            key = (s["part"], s["because"], json.dumps(s.get("dna"),
                                                      sort_keys=True),
                   s.get("lfo"), s.get("velocity_scale"))
            if key not in seen:                   # several conflicts, one fix
                seen.add(key)
                out.append(s)
        return out

    def _suggestion(self, c: Conflict, report: Report) -> Optional[dict]:
        a, b = c.parts[0], c.parts[-1]
        pa, pb = self.part(a), self.part(b)
        if c.kind == "masking":
            quiet = b if report.levels_db.get(a, 0) > report.levels_db.get(b, 0) else a
            loud = a if quiet == b else b
            # the sustained one steps back; the other keeps its character
            mover = loud if self.part(loud).legato > self.part(quiet).legato \
                else quiet
            return {"part": mover, "because": c.kind,
                    "dna": {"body": -0.3, "brightness": -0.2 if
                            self.part(mover).median < self.part(
                                a if mover == b else b).median else +0.1,
                            "decay": -0.2},
                    "velocity_scale": 0.8,
                    "say": f"let {mover} fall away sooner and step back "
                           f"~2 dB so {a if mover == b else b} shows through"}
        if c.kind in ("bass_stack", "mud"):
            mover = b if pb.median > pa.median else a
            return {"part": mover, "because": c.kind,
                    "dna": {"bass_weight": -0.45, "brightness": +0.1},
                    "octave": +1 if self.part(mover).median < 62 else 0,
                    "say": f"thin {mover}'s low end (or lift it an octave): "
                           f"the bass owns the bottom"}
        if c.kind == "sustain_blanket":
            return {"part": a, "because": c.kind,
                    "dna": {"body": -0.35, "decay": -0.25, "attack": +0.1,
                            "articulation": -0.2},
                    "say": f"shorten {a}'s sustain so {b} can be heard "
                           f"between its notes"}
        if c.kind == "onsets":
            return {"part": b, "because": c.kind,
                    "dna": {"attack": +0.3, "punch": -0.3},
                    "say": f"soften {b}'s attack so the two hits stop "
                           f"stacking"}
        if c.kind == "clash":
            return {"part": b, "because": c.kind,
                    "dna": {"metallicity": -0.4, "brightness": -0.15},
                    "say": f"make {b} smoother (less metallic) so the "
                           f"close intervals read as colour, not as an "
                           f"error"}
        if c.kind == "dynamics":
            gap = c.data.get("have_db", 0.0) - c.data.get("want_db", 0.0)
            gap = max(2.0, min(12.0, gap))
            return {"part": b, "because": c.kind,
                    "velocity_scale": round(10.0 ** (-gap / 20.0), 3),
                    "say": f"bring {b} down about {gap:.0f} dB under the "
                           f"lead"}
        if c.kind == "channel":
            return {"part": a, "because": c.kind,
                    "dna": {"detune": -1.0},
                    "say": f"give {a} no detune layer, or leave the "
                           f"channel above it free"}
        if c.kind == "lfo":
            part = (c.data.get("parts") or [b])[0]
            return {"part": part, "because": c.kind,
                    "lfo": c.data.get("rate"),
                    "say": f"put {part} on LFO rate {c.data.get('rate')}, "
                           f"the one most of the FM parts use (the chip "
                           f"has one)"}
        return None


def _loudness(fp) -> float:
    """Loudest stretch of a footprint: the peak of an 8-row moving mean of
    its total weighted energy. A part is as loud as its loudest moments, as
    calibrate_bank.py judged patches."""
    if not fp:
        return 0.0
    rows = sorted(fp)
    energy = {r: sum(fp[r][i] * BAND_WEIGHT[i] for i in range(BANDS))
              for r in rows}
    best = 0.0
    for r in rows:
        window = sum(energy.get(x, 0.0) for x in range(r, r + 8)) / 8.0
        best = max(best, window)
    return best


def _slots(backend: str, voice: str, voices: int):
    """The chip channels a voice with extra layers occupies."""
    if backend == "opl2" and voice.startswith("opl"):
        base = int(voice[3:])
        return [f"opl{base + k}" for k in range(voices)]
    if backend == "ym2612" and voice.startswith("fm"):
        base = int(voice[2:])
        return [f"fm{base + k}" for k in range(voices)]
    if backend == "nes":
        if voices > 1 and voice == "pulse1":
            return ["pulse1", "pulse2"]
        if voices > 1 and voice == "pulse2":
            return ["pulse2", "pulse1"]
        return [voice]
    return [voice]


# --------------------------------------------------------------------------
# Carrying a suggestion out
# --------------------------------------------------------------------------
def refit(arrangement: Arrangement, suggestion: dict, genome: Genome,
          backend=None):
    """Apply a suggestion's DNA change to a genome, realise it with real
    renders, and swap the result into the arrangement. -> (Genome,
    Compiled) and the arrangement now holds the new instrument."""
    part = suggestion["part"]
    backend_name, _ = arrangement.assignment[part]
    backend = backend or get_backend(backend_name)
    deltas = {a: d for a, d in suggestion.get("dna", {}).items() if a in AXES}
    if "detune" in deltas and deltas["detune"] <= -1.0:
        deltas["detune"] = -genome.dna.detune
    changed = genome.clone(dna=genome.dna.shifted(deltas), name=genome.name)
    # a learned correction belongs to the DNA it was learned at; an axis
    # taken to zero (no detune layer) must not be held up by it
    bias = {a: b for a, b in genome.bias.items()
            if not (a in deltas and getattr(changed.dna, a) <= 0.02)}
    changed = changed.clone(bias=bias)
    if deltas:
        g, compiled, _ = evolve.realise(backend, changed, probes=3,
                                        full=False)
    else:                                # nothing to re-render: a setting
        g = genome
        compiled = backend.from_dict(backend.to_dict(
            arrangement.assignment[part][1]))
    if suggestion.get("lfo") is not None and compiled.setup:
        compiled.setup["lfo"] = int(suggestion["lfo"])
    arrangement.assignment[part] = (backend_name, compiled)
    if "velocity_scale" in suggestion:
        arrangement.scales[part] = arrangement.scales.get(part, 1.0) \
            * suggestion["velocity_scale"]
    arrangement.invalidate(part)
    return g, compiled


def fit(arrangement: Arrangement, genomes: Dict[str, Genome],
        rounds: int = 4, verbose=None):
    """Greedy repair: take the worst conflict, apply its suggestion if the
    repaired arrangement costs less, keep going until nothing improves.
    Only parts whose genome is known can change. -> (Report, [applied])."""
    best = arrangement.analyse()
    applied = []
    for _ in range(rounds):
        moved = False
        for suggestion in arrangement.suggest(best):
            part = suggestion["part"]
            if part not in genomes:
                continue
            snapshot = (dict(arrangement.assignment), dict(arrangement.scales),
                        genomes[part])
            g, _ = refit(arrangement, suggestion, genomes[part])
            report = arrangement.analyse()
            if report.cost + 1e-6 < best.cost:
                genomes[part] = g
                applied.append(suggestion)
                best = report
                moved = True
                if verbose:
                    verbose(f"applied: {suggestion['say']} "
                            f"(cost {report.cost:.2f})")
                break
            arrangement.assignment, arrangement.scales = snapshot[0], snapshot[1]
            arrangement.invalidate(part)
        if not moved:
            break
    return best, applied
