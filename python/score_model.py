"""score_model.py — a score as notes per voice, which is what music analysis needs.

The event stream is the right representation for a chip and the wrong one
for musical analysis. An event says "key on at tick 4128"; a phrase is
made of notes with lengths, and half of what distinguishes a bass line
from a pad is how long the notes are held. corpus_digest.py works from
onsets alone and cannot see that.

So this walks the events once and pairs each key-on with its key-off,
quantised to the score's own rows. Everything downstream — role
classification, chord detection, pattern extraction — reads Notes.

Rows rather than seconds throughout. A pattern is "four notes on the
beat", not "four notes 125 ms apart"; keeping the unit musical is what
lets a 150 BPM pattern and a 180 BPM pattern be recognised as the same
pattern.
"""

import collections
import os
import sys
from typing import NamedTuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import events as events_mod
import tracker as tracker_mod


class Note(NamedTuple):
    row: int          #: onset, in rows from the start of the score
    length: int       #: duration in rows; at least 1
    pitch: int        #: MIDI-style number, octave * 12 + semitone
    velocity: int     #: 1-127 for FM, 0-15 for PSG (0 is loudest there)

    @property
    def end(self) -> int:
        return self.row + self.length


class Score(NamedTuple):
    voices: dict      #: voice name -> [Note], each sorted by row
    drums: list       #: rows a DAC sample was triggered on
    bar: int          #: rows per bar
    lpb: int          #: rows per beat
    bpm: float
    rows: int         #: total length in rows
    title: str
    path: str

    def fm_voices(self):
        return {name: notes for name, notes in self.voices.items()
                if name.startswith("fm")}


def _voice_of(event):
    E = events_mod
    if isinstance(event, E.FMNoteOn):
        return f"fm{event.channel}"
    if isinstance(event, E.PSGToneOn):
        return f"psg{event.channel}"
    if isinstance(event, E.OPLNoteOn):
        return f"opl{event.channel}"
    return None


def _voice_of_off(event):
    E = events_mod
    if isinstance(event, E.FMNoteOff):
        return f"fm{event.channel}"
    if isinstance(event, E.PSGToneOff):
        return f"psg{event.channel}"
    if isinstance(event, E.OPLNoteOff):
        return f"opl{event.channel}"
    return None


def from_events(events, meta, title: str = "", path: str = "") -> Score:
    ticks_per_row = meta.ticks_per_row()
    bar = max(1, meta.lpb * 4)
    voices = collections.defaultdict(list)
    drums = []
    open_notes = {}                 # voice -> (row, pitch, velocity)
    tick = 0
    E = events_mod

    def close(voice, at_row):
        start = open_notes.pop(voice, None)
        if start is None:
            return
        row, pitch, velocity = start
        voices[voice].append(
            Note(row=row, length=max(1, at_row - row), pitch=pitch,
                 velocity=velocity))

    for event in events:
        if isinstance(event, E.Wait):
            tick += event.ticks
            continue
        if isinstance(event, E.End):
            break
        row = tick // ticks_per_row

        if isinstance(event, E.DACSample):
            drums.append(row)
            continue

        voice = _voice_of_off(event)
        if voice is not None:
            close(voice, row)
            continue

        voice = _voice_of(event)
        if voice is None:
            continue
        # A key-on while the voice is already sounding ends the old note:
        # one channel plays one note, so the previous one is over whether
        # or not the score bothered to say so.
        close(voice, row)
        pitch = event.octave * 12 + events_mod.NOTE_NAMES.index(event.note)
        velocity = getattr(event, "velocity", None)
        if velocity is None:
            velocity = getattr(event, "volume", 127)
        open_notes[voice] = (row, pitch, velocity)

    final_row = tick // ticks_per_row
    for voice in list(open_notes):
        close(voice, final_row + 1)

    return Score(voices={name: sorted(notes, key=lambda n: (n.row, n.pitch))
                         for name, notes in voices.items()},
                 drums=drums, bar=bar, lpb=meta.lpb, bpm=meta.bpm,
                 rows=final_row, title=title or getattr(meta, "title", ""),
                 path=path)


def load(path: str) -> Score:
    with open(path, encoding="utf-8") as handle:
        events, meta = tracker_mod.loads(handle.read())
    return from_events(events, meta, path=path,
                       title=os.path.splitext(os.path.basename(path))[0])


def load_all(paths, limit: int = None):
    """Parse many scores, skipping the ones that will not parse."""
    out = []
    for path in (paths[:limit] if limit else paths):
        try:
            out.append(load(path))
        except Exception:
            continue
    return out


def corpus_paths(root: str = None):
    """Every .trk in the embedded corpus sector, sorted."""
    root = root or os.path.join(os.path.dirname(_HERE), "corpus")
    found = []
    for directory, _, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(".trk"):
                found.append(os.path.join(directory, name))
    return sorted(found)
