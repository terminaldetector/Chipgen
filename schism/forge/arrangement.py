"""arrangement.py — where an instrument can sit in a module, and what it does
to its neighbours.

An instrument that measures well alone can still ruin a song: a pad that
blankets the lead's midrange, two basses on one octave, a bell whose
partials land a semitone from the melody, a lead quieter than the arpeggio
under it, five parts piled in the middle of the stereo field. None of that is
visible in one instrument's dials. This module looks at the channels of a
module together:

* a **part profile** — the notes of one channel: register, density, how long
  its notes are held against the gaps, how hard it is played, the job it
  seems to do (bass, lead, pad, arp, harmony, drums);
* an **instrument profile** — what the probe measured when the instrument was
  played at the part's register: its spectrum in semitone bands, attack,
  fall, sustain, release, loudness. Any instrument can be probed: one a
  genome compiled, one a score's `inst` line made, one read out of a `.it`;
* a **spectral footprint** — the two put together: for every row of the song
  and each ear, how much energy the part puts in each of 24 Bark bands, from
  the instrument's own spectrum moved to the note's pitch and its envelope.
  No audio is rendered for this; one probe per instrument is the whole cost;
* the **conflicts** between footprints, named: masking, a bass stacked on a
  bass, a sustained part blanketing a busy one, onsets that land together,
  notes a semitone apart in two metallic voices, a part that drowns the
  lead, parts stacked in the middle, background voices piling up under a
  note-continue instrument.

`suggest` turns conflicts into changes (the DNA of a forged instrument, a
level, a pan, an octave); `fit` carries them out, with real probes, and
keeps a change only if the module's cost goes down.
"""

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .. import model as M
from . import evolve, probe as probe_mod
from .descriptors import SEMITONE_BANDS, SEMITONE_BASE_HZ
from .dna import AXES
from .family import get_family
from .patch import install

BANDS = 24
#: rough equal-loudness weighting per Bark band (the ear's low and very high
#: ends count for less)
BAND_WEIGHT = (0.35, 0.5, 0.65, 0.8, 0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
               1.0, 1.0, 0.95, 0.9, 0.85, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2)
#: how unpleasant two notes are at each interval (semitones mod 12)
DISSONANCE = (0.0, 1.0, 0.5, 0.1, 0.05, 0.15, 0.8, 0.02, 0.1, 0.05, 0.45, 0.9)
#: how loud each role wants to be against the lead, dB
ROLE_LEVEL_DB = {"lead": 0.0, "counter": -4.0, "harmony": -4.0, "arp": -6.0,
                 "pad": -7.0, "bass": -2.0, "drums": -3.0, "fx": -6.0}
#: A part whose median note is under this (D-3) and whose job is a guess is
#: the bass. (IT numbers C-0 as 1: D-3 is 39.)
BASS_BELOW = 40
#: voices that may sound at once before the mixer and the ear object
VOICE_LIMIT = 32

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def hz_of(note: int) -> float:
    """IT note number (1 = C-0, 58 = A-4) -> Hz."""
    return 440.0 * 2.0 ** ((note - 58) / 12.0)


def bark(f: float) -> float:
    return 13.0 * math.atan(0.00076 * f) + 3.5 * math.atan((f / 7500.0) ** 2)


def band_of(f: float) -> int:
    return max(0, min(BANDS - 1, int(bark(f))))


def band_edge_hz(band: int) -> float:
    lo, hi = 0.0, 24000.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if bark(mid) < band:
            lo = mid
        else:
            hi = mid
    return hi


LOW_BANDS = 3
LOW_HZ = band_edge_hz(LOW_BANDS)
#: the Bark band of each semitone band of the spectrum
_SEMI_TO_BARK = [band_of(SEMITONE_BASE_HZ * 2.0 ** (s / 12.0))
                 for s in range(SEMITONE_BANDS)]


def note_label(note: int) -> str:
    index = note - 1
    return f"{NOTE_NAMES[index % 12]}-{index // 12}"


# --------------------------------------------------------------------------
# Walking the song
# --------------------------------------------------------------------------
@dataclass
class Note:
    row: int                  # absolute row, counting every row played
    start: float              # seconds
    length_rows: int
    length_s: float
    pitch: int                # IT note number
    velocity: int             # 1..64
    instrument: int
    sample: int               # which sample plays it (0 = none)
    end_row: int = 0

    @property
    def end(self) -> int:
        return self.row + self.length_rows


@dataclass
class Song:
    """The module as played: its rows in order, with their times, and each
    channel's notes."""
    rows: int = 0
    seconds: float = 0.0
    row_s: List[float] = field(default_factory=list)       # seconds per row
    row_t: List[float] = field(default_factory=list)       # start of each row
    notes: Dict[int, List[Note]] = field(default_factory=dict)
    bar_rows: int = 16


def walk(module: M.Module) -> Song:
    """Play the order list once, the way `Module.seconds` does (speed, tempo,
    jumps and breaks), and collect the notes of every channel."""
    song = Song(bar_rows=max(4, module.rows_per_measure))
    speed, tempo = module.speed, module.tempo
    order = row = 0
    visited = set()
    time = 0.0
    active: Dict[int, Note] = {}
    instrument_of: Dict[int, int] = {}
    guard = 0
    while module.orders and guard < 400000:
        guard += 1
        if order >= len(module.orders):
            break
        index = module.orders[order]
        if index == 255:
            break
        if index == 254 or index >= len(module.patterns):
            order, row = order + 1, 0
            continue
        pattern = module.patterns[index]
        if row >= pattern.rows:
            order, row = order + 1, 0
            continue
        if (order, row) in visited:
            break
        visited.add((order, row))
        jump = brk = None
        repeat = 0
        for cell in pattern.cells[row]:
            letter = M.EFFECT_LETTERS[cell.effect] if cell.effect else ""
            if letter == "A" and cell.param:
                speed = cell.param
            elif letter == "T" and cell.param >= 0x20:
                tempo = cell.param
            elif letter == "B":
                jump = cell.param
            elif letter == "C":
                brk = cell.param
            elif letter == "S" and cell.param >> 4 == 0xE:
                repeat = max(repeat, cell.param & 0xF)
        seconds = speed * (1 + repeat) * 2.5 / tempo
        absolute = song.rows
        song.row_t.append(time)
        song.row_s.append(seconds)
        for channel, cell in enumerate(pattern.cells[row]):
            if cell.instrument:
                instrument_of[channel] = cell.instrument
            note = cell.note
            slides = cell.effect and M.EFFECT_LETTERS[cell.effect] == "G"
            if 1 <= note <= 120 and not slides:
                _close(active.pop(channel, None), absolute, time)
                number = instrument_of.get(channel, cell.instrument)
                sample = 0
                if number and number <= len(module.instruments):
                    sample = module.instruments[number - 1].note_map[
                        note - 1][1]
                vol = cell.volume if 0 <= cell.volume <= 64 else 64
                active[channel] = Note(absolute, time, 0, 0.0, note,
                                       max(1, vol), number, sample)
                song.notes.setdefault(channel, []).append(active[channel])
            elif note in (M.NOTE_OFF, M.NOTE_CUT, M.NOTE_FADE):
                _close(active.pop(channel, None), absolute, time)
        time += seconds
        song.rows += 1
        if jump is not None:
            order, row = jump, brk or 0
        elif brk is not None:
            order, row = order + 1, brk
        else:
            row += 1
            if row >= pattern.rows:
                order, row = order + 1, 0
    for note in active.values():
        _close(note, song.rows, time)
    song.seconds = time
    return song


def _close(note: Optional[Note], row: int, time: float):
    if note is not None and note.length_rows == 0:
        note.length_rows = max(1, row - note.row)
        note.length_s = max(0.0, time - note.start)


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------
@dataclass
class PartProfile:
    voice: str
    channel: int = 0
    notes: int = 0
    low: int = 0
    high: int = 0
    median: float = 0.0
    density: float = 0.0           # notes per bar
    mean_length: float = 0.0       # rows
    legato: float = 0.0            # share of the song the part is sounding
    velocity: float = 64.0
    role: str = ""
    role_confidence: float = 0.0
    onsets: frozenset = frozenset()
    pan: int = 32
    instruments: tuple = ()

    @classmethod
    def of(cls, voice: str, channel: int, notes: List[Note], song: Song,
           module: M.Module) -> "PartProfile":
        if not notes:
            return cls(voice, channel)
        pitches = sorted(n.pitch for n in notes)
        sounding = sum(n.length_rows for n in notes)
        bars = max(1.0, song.rows / song.bar_rows)
        used = {}
        for n in notes:
            used[n.instrument] = used.get(n.instrument, 0) + 1
        pan = module.channel_pan[channel]
        first = max(used, key=used.get)
        if first and first <= len(module.instruments):
            dp = module.instruments[first - 1].default_pan
            if dp is not None:
                pan = dp
        return cls(voice, channel, len(notes), pitches[0], pitches[-1],
                   float(pitches[len(pitches) // 2]), len(notes) / bars,
                   sounding / len(notes),
                   min(1.0, sounding / max(1, song.rows)),
                   sum(n.velocity for n in notes) / len(notes),
                   onsets=frozenset(n.row for n in notes),
                   pan=min(64, max(0, 32 if pan == 100 else pan)),
                   instruments=tuple(sorted(used)))


def classify(part: PartProfile, kit: bool, parts: List[PartProfile]):
    """-> (role, confidence). A guess from the shape of the notes; a caller
    who knows better says so with `roles=`."""
    if kit:
        return "drums", 0.9
    if part.notes < 4:
        return "fx" if part.notes else "", 0.3
    if part.median < BASS_BELOW:
        return "bass", 0.7
    pitched = [p for p in parts if p.notes >= 4 and p.median >= BASS_BELOW]
    top = max((p.median for p in pitched), default=0)
    if part.legato > 0.8 and part.density < 3.0 and part.mean_length >= 6:
        return "pad", 0.7
    if part.density >= 6.0 and part.high - part.low <= 14:
        return "arp", 0.6
    if part.median >= top - 3 and part.density >= 2.0:
        return "lead", 0.6
    if part.density >= 1.5 and part.median >= top - 12:
        return "counter", 0.4
    return "harmony", 0.4


@dataclass
class InstrumentProfile:
    name: str
    number: int
    sample: int
    axes: Dict[str, float]
    spectrum: List[float]          # share of the energy in each semitone band
    attack_s: float
    decay_s: Optional[float]
    sustain: float
    release_s: float
    level: float                   # window RMS against the reference level
    probe_note: int
    pitched: bool = True
    nna: int = 0
    fade: int = 0
    voices: int = 1


def profile_instrument(module: M.Module, number: int, note: int,
                       pitched: bool = True) -> InstrumentProfile:
    """Probe instrument `number` at `note`."""
    ins = module.instruments[number - 1]
    result = probe_mod.probe_instrument(module, number, note, full=False,
                                        pitched=pitched)
    m = result.measurement
    ref = get_family("tone").reference_level()
    sample = ins.note_map[note - 1][1]
    return InstrumentProfile(
        ins.name or f"Instrument {number}", number, sample,
        {k: v for k, v in m.axes.items() if v is not None},
        list(m.semitones) or [0.0] * SEMITONE_BANDS,
        m.raw.get("attack_s", 0.0), m.raw.get("decay_s"),
        m.raw.get("sustain_ratio", 1.0), m.raw.get("release_s", 0.1),
        result.level / ref, note, pitched, ins.nna, ins.fadeout)


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
def ear_gains(pan: int):
    """IT's pan law, measured: left (64-pan)/64, right pan/64."""
    return (64 - pan) / 64.0, pan / 64.0


def _bark_energy(p: InstrumentProfile, pitch: int) -> List[float]:
    """The instrument's spectrum moved to `pitch`, in the 24 Bark bands."""
    shift = pitch - p.probe_note if p.pitched else 0
    out = [0.0] * BANDS
    for s, v in enumerate(p.spectrum):
        if v <= 0.0:
            continue
        t = s + shift
        if 0 <= t < SEMITONE_BANDS:
            out[_SEMI_TO_BARK[t]] += v
    return out


def footprint(notes: List[Note], p_of, song: Song, pan: int,
              scale: float = 1.0):
    """{row: [24 left bands] + [24 right bands]} for one part. `p_of(note)`
    returns the InstrumentProfile that plays it."""
    left, right = ear_gains(pan)
    out: Dict[int, List[float]] = {}
    for n in notes:
        p = p_of(n)
        if p is None:
            continue
        bands = _bark_energy(p, n.pitch)
        vel = n.velocity / 64.0
        gain = scale * p.level * vel
        tail = max(p.release_s * 2.0, 0.1)
        t = 0.0
        r = n.row
        while r < song.rows:
            t = song.row_t[r] - n.start + 0.5 * song.row_s[r]
            if t > n.length_s + tail * 3.0:
                break
            e = envelope_at(p, t, n.length_s)
            if e > 1e-4:
                slot = out.setdefault(r, [0.0] * (2 * BANDS))
                w = (gain * e) ** 2
                for b in range(BANDS):
                    slot[b] += w * left * left * bands[b]
                    slot[BANDS + b] += w * right * right * bands[b]
            r += 1
    return out


def total(fp) -> float:
    return sum(sum(v) for v in fp.values())


def _weighted_total(fp) -> float:
    return sum(sum(v[i] * BAND_WEIGHT[i % BANDS] for i in range(2 * BANDS))
               for v in fp.values())


def overlap(a, b) -> float:
    """Similarity of two footprints in time x ear x band, in the space the
    ear weights (each band by how audible it is, numerator and norms
    alike): 0 = they never share a band at a time, 1 = the same."""
    num = 0.0
    for r, va in a.items():
        vb = b.get(r)
        if vb is None:
            continue
        for i in range(2 * BANDS):
            num += math.sqrt(va[i] * vb[i]) * BAND_WEIGHT[i % BANDS]
    ta, tb = _weighted_total(a), _weighted_total(b)
    if ta <= 0 or tb <= 0:
        return 0.0
    return min(1.0, num / math.sqrt(ta * tb))


def _loudness(fp) -> float:
    """Loudest stretch of a footprint: the peak of an 8-row moving mean of
    its total weighted energy."""
    if not fp:
        return 0.0
    energy = {r: sum(v[i] * BAND_WEIGHT[i % BANDS] for i in range(2 * BANDS))
              for r, v in fp.items()}
    best = 0.0
    for r in sorted(energy):
        window = sum(energy.get(x, 0.0) for x in range(r, r + 8)) / 8.0
        best = max(best, window)
    return best


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
    data: dict = field(default_factory=dict)

    def __str__(self):
        loc = f" ({self.where})" if self.where else ""
        return f"[{self.severity:.2f}] {self.kind}: {self.message}{loc}"


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
                f"  {v:6s} {p.notes:4d} notes  "
                f"{note_label(p.low)}-{note_label(p.high)}  "
                f"{p.density:4.1f}/bar  legato {p.legato:.2f}  "
                f"pan {p.pan:2d}  role {p.role or '?'}"
                + (f"  {i.name} {self.levels_db.get(v, 0):+.1f} dB"
                   if i else ""))
        if self.conflicts:
            lines.append("conflicts, worst first:")
            lines += ["  " + str(c) for c in
                      sorted(self.conflicts, key=lambda c: -c.severity)]
        else:
            lines.append("no conflicts found")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"cost": round(self.cost, 3),
                "parts": {v: {"notes": p.notes, "low": note_label(p.low),
                              "high": note_label(p.high),
                              "density": round(p.density, 2),
                              "legato": round(p.legato, 2), "role": p.role,
                              "pan": p.pan} for v, p in self.parts.items()},
                "levels_db": {k: round(v, 1)
                              for k, v in self.levels_db.items()},
                "conflicts": [{"kind": c.kind, "parts": list(c.parts),
                               "severity": round(c.severity, 3),
                               "message": c.message, "where": c.where}
                              for c in sorted(self.conflicts,
                                              key=lambda c: -c.severity)]}


def voice_name(channel: int) -> str:
    return f"ch{channel + 1:02d}"


class Arrangement:
    """A module and the analysis of how its channels sit."""

    def __init__(self, module: M.Module, roles: Optional[Dict[str, str]] = None,
                 scales: Optional[Dict[str, float]] = None,
                 forged: Optional[dict] = None):
        """`roles` names a channel's job (bass, lead, harmony, arp, pad,
        counter, drums, fx) when the shape of its notes does not say it
        clearly enough. `forged` maps an instrument number to the
        (Genome, Compiled) a forge made it from, which lets `fit` change
        its DNA."""
        self.module = module
        self.song = walk(module)
        self.roles = dict(roles or {})
        self.scales = dict(scales or {})
        self.forged = dict(forged if forged is not None
                           else getattr(module, "forged", {}) or {})
        self._parts: Dict[str, PartProfile] = {}
        self._profiles: Dict[tuple, InstrumentProfile] = {}
        self._fps: Dict[str, dict] = {}

    # -- building ----------------------------------------------------------------
    def voices(self) -> List[str]:
        return [voice_name(c) for c in sorted(self.song.notes)
                if self.song.notes[c]]

    def channel_of(self, voice: str) -> int:
        return int(voice[2:]) - 1

    def _is_kit(self, number: int) -> bool:
        """An instrument that plays several different samples at one pitch
        each is a kit."""
        if not number or number > len(self.module.instruments):
            return False
        targets = {t for t, s in self.module.instruments[number - 1].note_map
                   if s}
        return len(targets) == 1 and any(
            s for _, s in self.module.instruments[number - 1].note_map)

    def part(self, voice: str) -> PartProfile:
        if voice not in self._parts:
            channel = self.channel_of(voice)
            p = PartProfile.of(voice, channel, self.song.notes.get(channel, []),
                               self.song, self.module)
            if voice in self.roles:
                p.role, p.role_confidence = self.roles[voice], 1.0
            else:
                kit = bool(p.instruments) and self._is_kit(p.instruments[0])
                others = [self.part(v) for v in self.voices()
                          if v != voice and v in self._parts]
                p.role, p.role_confidence = classify(p, kit, others + [p])
            self._parts[voice] = p
        return self._parts[voice]

    def _profile_for(self, voice: str, note: Note) -> Optional[InstrumentProfile]:
        if not note.instrument or not note.sample:
            return None
        part = self.part(voice)
        kit = self._is_kit(note.instrument)
        if kit:
            probe_note = note.pitch
        else:
            same = sorted(n.pitch for n in self.song.notes[part.channel]
                          if n.instrument == note.instrument
                          and n.sample == note.sample)
            probe_note = same[len(same) // 2]
        key = (note.instrument, note.sample, probe_note if kit else
               probe_note // 3)
        if key not in self._profiles:
            try:
                self._profiles[key] = profile_instrument(
                    self.module, note.instrument, probe_note, pitched=not kit)
            except Exception:                 # an unplayable note is no profile
                self._profiles[key] = None
        return self._profiles[key]

    def instrument(self, voice: str) -> Optional[InstrumentProfile]:
        """The profile of the instrument a part mostly plays."""
        notes = self.song.notes.get(self.channel_of(voice), [])
        if not notes:
            return None
        counts = {}
        for n in notes:
            counts[n.instrument] = counts.get(n.instrument, 0) + 1
        main = max(counts, key=counts.get)
        for n in notes:
            if n.instrument == main:
                return self._profile_for(voice, n)
        return None

    def footprint(self, voice: str):
        if voice not in self._fps:
            part = self.part(voice)
            channel = part.channel
            self._fps[voice] = footprint(
                self.song.notes.get(channel, []),
                lambda n, v=voice: self._profile_for(v, n), self.song,
                part.pan, self.scales.get(voice, 1.0))
        return self._fps[voice]

    def invalidate(self, voice=None):
        if voice is None:
            self._parts.clear()
            self._profiles.clear()
            self._fps.clear()
            self.song = walk(self.module)
            return
        self._parts.pop(voice, None)
        self._fps.pop(voice, None)
        part_instruments = set(self.part(voice).instruments) \
            if voice in self._parts else set()
        for key in [k for k in self._profiles if k[0] in part_instruments]:
            self._profiles.pop(key, None)

    # -- analysis -------------------------------------------------------------------------
    def analyse(self, measure: bool = False) -> Report:
        voices = self.voices()
        report = Report()
        for v in voices:
            report.parts[v] = self.part(v)
            ip = self.instrument(v)
            if ip is not None:
                report.instruments[v] = ip
        fps = {v: self.footprint(v) for v in voices}
        loud = {v: _loudness(fps[v]) for v in voices}
        ref = max(loud.values(), default=1.0) or 1.0
        report.levels_db = {v: 10.0 * math.log10(max(loud[v], 1e-12) / ref)
                            for v in voices}
        if measure:
            self._real_levels(report, voices)
            loud = {v: 10.0 ** (report.levels_db[v] / 10.0) for v in voices}
        self._masking(report, voices, fps)
        self._bass(report, voices, fps)
        self._blanket(report, voices)
        self._onsets(report, voices)
        self._clash(report, voices)
        self._dynamics(report, voices, loud)
        self._stereo(report, voices, fps)
        self._polyphony(report, voices)
        return report

    def _real_levels(self, report, voices):
        """Replace the estimated levels with the real ones: each channel
        rendered alone by libopenmpt (levels.py)."""
        from .. import levels
        try:
            rows = levels.voice_levels(self.module)
        except Exception:
            return
        by = {voice_name(r["channel"] - 1): r["rms_db"] for r in rows}
        top = max(by.values(), default=0.0)
        for v in voices:
            if v in by:
                report.levels_db[v] = by[v] - top

    def _masking(self, report, voices, fps):
        for i, a in enumerate(voices):
            for b in voices[i + 1:]:
                o = overlap(fps[a], fps[b])
                if o < 0.55:
                    continue
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

    def _low(self, fp):
        """A footprint with only its lowest bands, for the bass checks."""
        keep = LOW_BANDS + 2
        return {r: s[:keep] + [0.0] * (BANDS - keep)
                + s[BANDS:BANDS + keep] + [0.0] * (BANDS - keep)
                for r, s in fp.items()}

    def _bass(self, report, voices, fps):
        lows = [v for v in voices if self.part(v).median < BASS_BELOW
                and self.part(v).notes >= 4
                and self.part(v).role != "drums"]
        if len(lows) >= 2:
            a, b = sorted(lows, key=lambda v: self.part(v).median)[:2]
            o = overlap(self._low(fps[a]), self._low(fps[b]))
            if o > 0.4:
                report.conflicts.append(Conflict(
                    "bass_stack", (a, b), min(1.0, o),
                    f"{a} and {b} both live under {note_label(BASS_BELOW)} "
                    f"and share their low bands ({o:.0%}): the bottom turns "
                    f"to mud"))
        if lows:
            bass = min(lows, key=lambda v: self.part(v).median)
            bass_low = sum(sum(s[:LOW_BANDS]) + sum(s[BANDS:BANDS + LOW_BANDS])
                           for s in fps[bass].values())
            for v in voices:
                if v == bass or self.part(v).role == "drums":
                    continue
                other = sum(sum(s[:LOW_BANDS]) + sum(s[BANDS:BANDS + LOW_BANDS])
                            for s in fps[v].values())
                if bass_low > 0 and other > 0.35 * bass_low \
                        and self.part(v).median >= BASS_BELOW:
                    report.conflicts.append(Conflict(
                        "mud", (bass, v), min(1.0, other / bass_low),
                        f"{v} puts {other / bass_low:.0%} as much energy "
                        f"under {LOW_HZ:.0f} Hz as the bass does, from a "
                        f"register that should not be there"))

    def _blanket(self, report, voices):
        """A part that holds long notes over a part that moves."""
        for a in voices:
            pa = self.part(a)
            ia = self.instrument(a)
            if ia is None or pa.legato < 0.75 or ia.sustain < 0.5:
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
                ia, ib = self.instrument(a), self.instrument(b)
                if not pa.onsets or not pb.onsets or ia is None or ib is None:
                    continue
                if "drums" in (pa.role, pb.role):
                    continue
                same = len(pa.onsets & pb.onsets) / min(len(pa.onsets),
                                                        len(pb.onsets))
                sharp = ia.attack_s < 0.012 and ib.attack_s < 0.012
                if same >= 0.7 and sharp and pa.notes >= 6:
                    report.conflicts.append(Conflict(
                        "onsets", (a, b), min(1.0, (same - 0.5)),
                        f"{same:.0%} of the notes of {a} and {b} start on "
                        f"the same row and both attack in under 12 ms: the "
                        f"hits stack into one"))

    def _clash(self, report, voices):
        for i, a in enumerate(voices):
            for b in voices[i + 1:]:
                na = self.song.notes.get(self.channel_of(a), [])
                nb = self.song.notes.get(self.channel_of(b), [])
                ia, ib = self.instrument(a), self.instrument(b)
                if not na or not nb or ia is None or ib is None:
                    continue
                if "drums" in (self.part(a).role, self.part(b).role) \
                        or not (ia.pitched and ib.pitched):
                    continue
                grain = (1.0 + ia.axes.get("metallicity", 0)
                         + ib.axes.get("metallicity", 0))
                events = 0
                total_w = 0.0
                worst, at, count = 0.0, None, 0
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
        by_role = {v: self.part(v).role or ("bass" if self.part(v).median
                                            < BASS_BELOW else "harmony")
                   for v in voices}
        leads = [v for v in voices if by_role[v] == "lead"]
        if not leads:
            return
        lead = max(leads, key=lambda v: loud[v])
        for v in voices:
            if v == lead:
                continue
            want = ROLE_LEVEL_DB.get(by_role[v], -4.0)
            have = 10 * math.log10(max(loud[v], 1e-12)
                                   / max(loud[lead], 1e-12))
            if have > want + 3.0:
                report.conflicts.append(Conflict(
                    "dynamics", (lead, v), min(1.0, (have - want) / 12.0),
                    f"{v} ({by_role[v]}) is {have:+.1f} dB against the lead "
                    f"{lead}; a {by_role[v]} belongs near {want:+.0f} dB",
                    data={"have_db": have, "want_db": want}))

    def _stereo(self, report, voices, fps):
        """Parts that share the middle and each other's spectrum."""
        centred = [v for v in voices if abs(self.part(v).pan - 32) <= 6
                   and self.part(v).role not in ("bass", "drums")]
        if len(centred) < 3:
            return
        pairs = []
        for i, a in enumerate(centred):
            for b in centred[i + 1:]:
                o = overlap(fps[a], fps[b])
                if o > 0.4:
                    pairs.append((o, a, b))
        if len(pairs) >= 2:
            names = sorted({n for _, a, b in pairs for n in (a, b)})
            sev = min(1.0, 0.25 * len(pairs))
            report.conflicts.append(Conflict(
                "pan_stack", tuple(names), sev,
                f"{', '.join(names)} all sit in the middle and share their "
                f"spectrum; moving them apart in the stereo field costs "
                f"nothing and gives each room",
                data={"parts": names}))

    def _polyphony(self, report, voices):
        """Background voices: a note that keeps sounding under the next one
        on the same channel (NNA continue/off/fade) is a voice of its own
        until it dies."""
        events = []
        for v in voices:
            ip = self.instrument(v)
            if ip is None:
                continue
            for n in self.song.notes.get(self.channel_of(v), []):
                tail = n.length_s
                if ip.nna != 0:
                    tail = n.length_s + (2048.0 / max(ip.fade, 1) * 0.02
                                         if ip.fade else ip.release_s * 3.0)
                events.append((n.start, +1))
                events.append((n.start + max(tail, 0.02), -1))
        events.sort()
        now = peak = 0
        for _, delta in events:
            now += delta
            peak = max(peak, now)
        if peak > VOICE_LIMIT:
            report.conflicts.append(Conflict(
                "voices", tuple(voices[:2]),
                min(1.0, (peak - VOICE_LIMIT) / VOICE_LIMIT + 0.3),
                f"up to {peak} voices sound at once (the module plays "
                f"{VOICE_LIMIT} in a modest player); shorten the fadeout of "
                f"the instruments with NNA continue/fade, or use NNA cut",
                data={"peak": peak}))

    # -- suggestions ------------------------------------------------------------------------
    def suggest(self, report: Optional[Report] = None) -> List[dict]:
        report = report or self.analyse()
        out, seen = [], set()
        for c in sorted(report.conflicts, key=lambda c: -c.severity):
            for s in self._suggestions(c, report):
                key = (s["part"], s["because"],
                       json.dumps(s.get("dna"), sort_keys=True),
                       s.get("pan"), s.get("velocity_scale"),
                       s.get("octave"))
                if key not in seen:
                    seen.add(key)
                    out.append(s)
        return out

    def _forged_dna(self, voice):
        ip = self.instrument(voice)
        return ip is not None and ip.number in self.forged

    def _suggestions(self, c: Conflict, report: Report) -> List[dict]:
        a, b = c.parts[0], c.parts[-1]
        pa, pb = self.part(a), self.part(b)
        out = []
        if c.kind == "masking":
            quiet = b if report.levels_db.get(a, 0) > report.levels_db.get(b, 0) else a
            loud = a if quiet == b else b
            mover = loud if self.part(loud).legato > self.part(quiet).legato \
                else quiet
            other = a if mover == b else b
            # apart in the field first: it costs nothing
            if abs(self.part(mover).pan - self.part(other).pan) < 16:
                side = -1 if self.part(mover).pan <= self.part(other).pan else 1
                target = max(4, min(60, self.part(other).pan + side * 20))
                out.append({"part": mover, "because": c.kind, "pan": target,
                            "say": f"pan {mover} to {target} so "
                                   f"{other} shows through"})
            out.append({"part": mover, "because": c.kind,
                        "dna": {"body": -0.3, "decay": -0.2,
                                "brightness": -0.2 if self.part(mover).median
                                < self.part(other).median else +0.1},
                        "velocity_scale": 0.8,
                        "say": f"let {mover} fall away sooner and step back "
                               f"~2 dB so {other} shows through"})
        elif c.kind in ("bass_stack", "mud"):
            mover = b if pb.median > pa.median else a
            out.append({"part": mover, "because": c.kind,
                        "dna": {"bass_weight": -0.45, "brightness": +0.1},
                        "octave": +1 if self.part(mover).median < 62 else 0,
                        "say": f"thin {mover}'s low end (or lift it an "
                               f"octave): the bass owns the bottom"})
        elif c.kind == "sustain_blanket":
            out.append({"part": a, "because": c.kind,
                        "dna": {"body": -0.4, "decay": -0.35, "attack": +0.1,
                                "articulation": -0.25},
                        "velocity_scale": 0.85,
                        "say": f"shorten {a}'s sustain so {b} can be heard "
                               f"between its notes"})
        elif c.kind == "onsets":
            out.append({"part": b, "because": c.kind,
                        "dna": {"attack": +0.3, "punch": -0.3},
                        "say": f"soften {b}'s attack so the two hits stop "
                               f"stacking"})
        elif c.kind == "clash":
            out.append({"part": b, "because": c.kind,
                        "dna": {"metallicity": -0.4, "brightness": -0.15},
                        "say": f"make {b} smoother (less metallic) so the "
                               f"close intervals read as colour, not as an "
                               f"error"})
        elif c.kind == "dynamics":
            gap = c.data.get("have_db", 0.0) - c.data.get("want_db", 0.0)
            gap = max(2.0, min(12.0, gap))
            out.append({"part": b, "because": c.kind,
                        "velocity_scale": round(10.0 ** (-gap / 20.0), 3),
                        "say": f"bring {b} down about {gap:.0f} dB under the "
                               f"lead"})
        elif c.kind == "pan_stack":
            parts = c.data.get("parts", [])
            spread = [8, 56, 20, 44, 14, 50]
            for part, pan in zip(parts, spread):
                out.append({"part": part, "because": c.kind, "pan": pan,
                            "say": f"pan {part} to {pan}"})
        elif c.kind == "voices":
            out.append({"part": a, "because": c.kind, "nna": "cut",
                        "say": "give the instrument NNA cut"})
        return out

    # -- applying a suggestion to the module ----------------------------------------------
    def snapshot(self):
        return (list(self.module.channel_pan), list(self.module.channel_volume),
                copy.deepcopy(self.module.patterns), dict(self.scales),
                copy.deepcopy(self.module.instruments),
                list(self.module.samples), dict(self.forged))

    def restore(self, snap):
        (self.module.channel_pan, self.module.channel_volume,
         self.module.patterns, self.scales, self.module.instruments,
         self.module.samples, self.forged) = snap
        self.invalidate()

    def apply(self, suggestion: dict, genomes: Optional[dict] = None):
        """Carry one suggestion out on the module. -> True if anything
        changed. DNA changes need the forged instrument's genome."""
        part = suggestion["part"]
        channel = self.channel_of(part)
        changed = False
        if "pan" in suggestion:
            self.module.channel_pan[channel] = int(suggestion["pan"])
            changed = True
        if "velocity_scale" in suggestion:
            cur = self.module.channel_volume[channel]
            self.module.channel_volume[channel] = max(
                1, min(64, int(round(cur * suggestion["velocity_scale"]))))
            changed = True
        if suggestion.get("octave"):
            changed |= self._transpose(channel, 12 * suggestion["octave"])
        if "nna" in suggestion:
            ip = self.instrument(part)
            if ip is not None:
                self.module.instruments[ip.number - 1].nna = 0
                changed = True
        deltas = {k: v for k, v in suggestion.get("dna", {}).items()
                  if k in AXES}
        if deltas:
            ip = self.instrument(part)
            if ip is not None and ip.number in self.forged:
                changed |= self._redo(ip.number, deltas)
            elif ip is not None:
                changed |= edit_instrument(
                    self.module.instruments[ip.number - 1], deltas,
                    2.5 / self.module.tempo)
        if changed:
            self.invalidate()
        return changed

    def _transpose(self, channel: int, semitones: int) -> bool:
        moved = False
        for pattern in self.module.patterns:
            for row in pattern.cells:
                if channel < len(row):
                    cell = row[channel]
                    if 1 <= cell.note <= 120 and 1 <= cell.note + semitones <= 120:
                        cell.note += semitones
                        moved = True
        return moved

    def _redo(self, number: int, deltas: dict) -> bool:
        genome, compiled = self.forged[number]
        if genome.family == "layer":
            return False         # a stack has no dials; its parts do
        family = get_family(genome.family)
        changed = genome.clone(dna=genome.dna.shifted(deltas),
                               name=genome.name)
        bias = {a: b for a, b in genome.bias.items()
                if not (a in deltas and getattr(changed.dna, a) <= 0.02)}
        changed = changed.clone(bias=bias)
        g, new, _ = evolve.realise(family, changed, probes=3, full=False)
        tick = 2.5 / self.module.tempo
        install(new.patch, self.module, number, tick,
                self.module.instruments[number - 1].name)
        self.forged[number] = (g, new)
        compact_samples(self.module)
        return True


def edit_instrument(ins: M.Instrument, deltas: dict, tick: float) -> bool:
    """Move the dials of an instrument nobody forged, as far as its
    switches reach without making its samples again: the volume envelope
    for attack, body, decay and articulation, the filter for brightness
    (down only: a filter cannot add what the sample does not have).
    -> True if anything changed."""
    changed = False
    if "brightness" in deltas and deltas["brightness"] < -0.01:
        cutoff = 127 if ins.cutoff is None else ins.cutoff
        new = max(8, cutoff + int(round(96.0 * deltas["brightness"])))
        if new != cutoff:
            ins.cutoff = new
            if ins.resonance is None:
                ins.resonance = 0
            changed = True
    env_keys = {k: deltas[k] for k in ("attack", "body", "decay",
                                       "articulation") if k in deltas}
    if env_keys:
        changed |= _edit_envelope(ins, env_keys, tick)
    return changed


def _edit_envelope(ins: M.Instrument, d: dict, tick: float) -> bool:
    env = ins.volume_envelope
    if env is None or not env.nodes:
        env = M.Envelope(nodes=[(0, 64)], sustain=(0, 0))
    else:
        env = M.Envelope(nodes=list(env.nodes), loop=env.loop,
                         sustain=env.sustain, enabled=True)
    nodes = [list(n) for n in env.nodes]
    hold = env.sustain[0] if env.sustain else len(nodes) - 1
    peak = max(v for _, v in nodes[:hold + 1]) or 64
    changed = False
    head = [list(n) for n in nodes[:hold]] or [[nodes[0][0], peak]]
    # body / decay: the held level comes down, by a fall from the peak
    drop = d.get("body", 0.0) + 0.5 * d.get("decay", 0.0)
    if drop < -0.02:
        level = max(0.06, (nodes[hold][1] / float(peak))
                    * 10.0 ** (40.0 * drop / 20.0))
        tau = max(2, int(round(0.25 / tick)))        # a fall of about 250 ms
        start = head[-1][0]
        fall = []
        for x in (0.5, 1.0, 1.5, 2.5, 4.0):
            t = start + int(round(x * tau * 0.6))
            v = level * peak + (peak - level * peak) * math.exp(-x)
            fall.append([t, int(round(v))])
        end = start + int(round(4.0 * tau * 0.6)) + 1
        held = [end, max(1, int(round(level * peak)))]
        rest = nodes[hold + 1:]
        shift = held[0] - nodes[hold][0]
        nodes = head + fall + [held] + [[t + shift, v] for t, v in rest]
        nodes = _dedupe(nodes)
        hold = min(len(nodes) - 1, len(head) + len(fall))
        env.sustain = (hold, hold)
        changed = True
    # attack: a ramp from silence
    rise = d.get("attack", 0.0)
    if rise > 0.02:
        ticks = max(1, int(round(min(0.4, 0.02 + 0.3 * rise) / tick)))
        if nodes[0][0] == 0 and nodes[0][1] >= 0.9 * peak:
            first = nodes[0][1]
            nodes = [[0, 0], [ticks, first]] + [[t + ticks, v]
                                                for t, v in nodes[1:]]
            if env.sustain:
                env.sustain = (env.sustain[0] + 1, env.sustain[1] + 1)
            changed = True
    # articulation: a shorter release after the sustain
    shorter = d.get("articulation", 0.0)
    if shorter < -0.02 and env.sustain:
        sustain_at = env.sustain[1]
        tail = nodes[sustain_at + 1:]
        if tail:
            t0 = nodes[sustain_at][0]
            factor = 10.0 ** (1.7 * shorter)
            nodes = nodes[:sustain_at + 1] + [
                [t0 + max(1, int(round((t - t0) * factor))), v]
                for t, v in tail]
            nodes = _dedupe(nodes)
            changed = True
        else:
            # no release at all: add one, 120 ms
            t0 = nodes[sustain_at][0]
            nodes += [[t0 + max(1, int(round(0.06 / tick))),
                       int(nodes[sustain_at][1] * 0.2)],
                      [t0 + max(2, int(round(0.12 / tick))), 0]]
            changed = True
    if changed:
        if len(nodes) > 25:
            nodes = nodes[:24] + [nodes[-1]]
            if env.sustain and env.sustain[1] >= len(nodes):
                env.sustain = None
        env.nodes = [(int(t), max(0, min(64, int(v)))) for t, v in nodes]
        ins.volume_envelope = env
    return changed


def _dedupe(nodes):
    out = []
    for t, v in nodes:
        if out and t <= out[-1][0]:
            t = out[-1][0] + 1
        out.append([t, v])
    return out


def compact_samples(module: M.Module):
    """Drop the samples no instrument refers to (a replaced instrument leaves
    its old ones behind) and renumber the rest."""
    used = sorted({s for ins in module.instruments for _, s in ins.note_map
                   if s})
    remap = {old: new for new, old in enumerate(used, 1)}
    module.samples = [module.samples[old - 1] for old in used]
    for ins in module.instruments:
        ins.note_map = [(t, remap.get(s, 0)) for t, s in ins.note_map]


def _scaled(suggestion: dict, k: float) -> dict:
    """The same change at `k` times the strength."""
    out = dict(suggestion)
    if "dna" in suggestion:
        out["dna"] = {a: d * k for a, d in suggestion["dna"].items()}
    if "velocity_scale" in suggestion:
        out["velocity_scale"] = suggestion["velocity_scale"] ** k
    return out


def fit(arrangement: Arrangement, rounds: int = 4, verbose=None,
        measure: bool = False, min_gain: float = 0.03, breadth: int = 5,
        strengths=(1.0, 1.8)):
    """Greedy repair. Each round tries the `breadth` most pressing
    suggestions, each at every strength, on the module itself (with real
    probes, so a change to a forged instrument is made and heard, not
    predicted), puts each back, and keeps the one that lowers the cost the
    most — if it lowers it by `min_gain` or more. Goes on until nothing
    does. -> (Report, [applied])."""
    best = arrangement.analyse(measure)
    applied = []
    for _ in range(rounds):
        trials = []
        for suggestion in arrangement.suggest(best)[:breadth]:
            for k in strengths:
                trial = _scaled(suggestion, k) if k != 1.0 else suggestion
                if k != 1.0 and not (trial.get("dna")
                                     or "velocity_scale" in trial):
                    continue
                snap = arrangement.snapshot()
                try:
                    changed = arrangement.apply(trial)
                    cost = arrangement.analyse(measure).cost if changed \
                        else None
                except Exception:
                    cost = None
                arrangement.restore(snap)
                if cost is not None:
                    trials.append((cost, len(trials), trial))
        trials = [t for t in trials if t[0] + min_gain < best.cost]
        if not trials:
            break
        cost, _, chosen = min(trials, key=lambda t: (t[0], t[1]))
        arrangement.apply(chosen)
        best = arrangement.analyse(measure)
        applied.append(chosen)
        if verbose:
            verbose(f"applied: {chosen['say']} (cost {best.cost:.2f})")
    return best, applied
