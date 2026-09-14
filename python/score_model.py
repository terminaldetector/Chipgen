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
    # The NES names its voices rather than numbering them, and so does
    # the NES corpus. Without this a score written for the NES could be
    # emitted but never read back — the asymmetry went unnoticed because
    # nes_transcribe.py builds its Scores directly, never through here.
    if isinstance(event, E.NESNoteOn):
        return event.voice
    if isinstance(event, E.NESNoiseOn):
        return "noise"
    # The SN76489's noise, same gap as the NES's: a noise track written
    # out came back as nothing at all on the way in.
    if isinstance(event, E.PSGNoiseOn):
        return "noise"
    return None


def _voice_of_off(event):
    E = events_mod
    if isinstance(event, E.FMNoteOff):
        return f"fm{event.channel}"
    if isinstance(event, E.PSGToneOff):
        return f"psg{event.channel}"
    if isinstance(event, E.OPLNoteOff):
        return f"opl{event.channel}"
    if isinstance(event, E.NESNoteOff):
        return event.voice
    if isinstance(event, E.NESNoiseOff):
        return "noise"
    if isinstance(event, E.PSGNoiseOff):
        return "noise"
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

        if isinstance(event, (E.DACSample, E.NESSample)):
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
        pitch = (getattr(event, "octave", 0) * 12
                 + events_mod.NOTE_NAMES.index(getattr(event, "note", "C")))
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


#: Score voice name -> the NES voice the chip and the effect engine use.
#: A Score names NES voices the way the transcriber found them
#: ("pulse1"), the tracker numbers them ("nes0"); both arrive here.
_NES_VOICES = {"nes0": "pulse1", "nes1": "pulse2", "nes2": "triangle",
               "pulse1": "pulse1", "pulse2": "pulse2", "triangle": "triangle"}


def _noise_period(pitch: int) -> int:
    """A Score pitch -> the NES noise channel's 0-15 period index.

    The noise channel has no note, it has a timer index into a fixed
    table, so this picks the index whose shift rate is closest to the
    pitch the Score stored. It is a one-way trip: `nes_transcribe.py`
    derives that pitch from the raw nibble rather than the table (its own
    docstring calls the noise track unreliable), so a noise voice does
    not come back through here at the pitch it went out at. The rows and
    the velocities do, which is what percussion is carrying.
    """
    import nes_apu
    frequency = 440.0 * (2.0 ** ((pitch - 57) / 12.0))
    if frequency <= 0:
        return 4
    return min(range(16),
               key=lambda i: abs(nes_apu.NTSC_CPU_CLOCK
                                 / nes_apu.NOISE_PERIODS_NTSC[i] - frequency))


def is_nes(score: Score) -> bool:
    """Whether a score's voices name the NES.

    `noise` is a channel on the SN76489 AND on the NES, so the name alone
    cannot say which chip a voice belongs to. The company it keeps can: a
    score holding `pulse1` or `nes0` is an NES score.
    """
    return any(voice.lower() in _NES_VOICES for voice in score.voices)


def _note_on(voice: str, note: Note, nes: bool = True):
    E = events_mod
    name = E.NOTE_NAMES[note.pitch % 12]
    octave = note.pitch // 12
    if voice.startswith("fm"):
        return E.FMNoteOn(channel=int(voice[2:]), note=name, octave=octave,
                          velocity=note.velocity)
    if voice.startswith("opl"):
        return E.OPLNoteOn(channel=int(voice[3:]), note=name, octave=octave,
                           velocity=note.velocity)
    if voice.startswith("psg"):
        # The PSG's field is an attenuator, not a fader: the Score stores
        # whatever the source wrote, and 0 is loudest there.
        return E.PSGToneOn(channel=int(voice[3:]), note=name, octave=octave,
                           volume=note.velocity)
    if voice in _NES_VOICES:
        return E.NESNoteOn(voice=_NES_VOICES[voice], note=name,
                           octave=octave, velocity=note.velocity)
    if voice == "nes3" or (voice == "noise" and nes):
        return E.NESNoiseOn(period=_noise_period(note.pitch),
                            velocity=note.velocity)
    if voice in ("noise", "psgnoise"):
        # The SN76489's noise has three fixed rates and no notes, so
        # there is no pitch to carry across — white noise at the middle
        # rate, with the velocity doing the work. `arrange.py` reports
        # that the pitch does not survive rather than inventing one.
        return E.PSGNoiseOn(white=True, rate=1, volume=note.velocity)
    return None


def _note_off(voice: str, nes: bool = True):
    E = events_mod
    if voice.startswith("fm"):
        return E.FMNoteOff(channel=int(voice[2:]))
    if voice.startswith("opl"):
        return E.OPLNoteOff(channel=int(voice[3:]))
    if voice.startswith("psg"):
        return E.PSGToneOff(channel=int(voice[3:]))
    if voice in _NES_VOICES:
        return E.NESNoteOff(voice=_NES_VOICES[voice])
    if voice == "nes3" or (voice == "noise" and nes):
        return E.NESNoiseOff()
    if voice in ("noise", "psgnoise"):
        return E.PSGNoiseOff()
    return None


def to_events(score: Score, ticks_per_row: int = None, drum: str = "kick",
              drum_voice: str = None):
    """A Score back to events — the inverse of `from_events`.

    Music arrives from outside as a Score (a transcription, an
    arrangement, a generator); events are the only thing this engine
    renders, so something has to close the loop.

    Two things do not survive the round trip, and both are limits of the
    Score model rather than of this function. A drum hit is stored as a
    bare row with no sample name, so every hit comes back as `drum`. And
    per-note effects were never in a Score at all.

    `drum_voice` is "dac" or "nes"; by default a score holding NES voices
    gets the DMC and everything else the Genesis DAC.
    """
    E = events_mod
    if ticks_per_row is None:
        # The row grid has to match the score's own tempo, or every row
        # index is rescaled on the way back in and the music comes out
        # at a different length. Measured: emitting at a flat 24 ticks a
        # row and reading back at the score's 19 stretched every note by
        # 26%.
        meta = tracker_mod.Metadata()
        meta.bpm, meta.lpb = score.bpm, score.lpb
        ticks_per_row = meta.ticks_per_row()
    nes = is_nes(score)
    rows = {}
    unassigned = []
    for voice, notes in score.voices.items():
        ordered = sorted(notes, key=lambda n: n.row)
        for index, note in enumerate(ordered):
            on = _note_on(voice, note, nes)
            if on is None:
                unassigned.append(voice)
                break
            rows.setdefault(note.row, []).append(on)
            end = note.row + max(1, note.length)
            # One channel holds one note: a key-off placed after the next
            # note has already begun would silence that next note instead
            # of this one, so an overlap ends where the next note starts.
            if index + 1 < len(ordered):
                end = min(end, ordered[index + 1].row)
            off = _note_off(voice, nes)
            if off is not None and end > note.row:
                rows.setdefault(end, []).append(off)

    if drum_voice is None:
        drum_voice = "nes" if nes else "dac"
    for row in score.drums:
        rows.setdefault(row, []).append(
            E.NESSample(name=drum) if drum_voice == "nes"
            else E.DACSample(name=drum))

    if unassigned:
        # Silence is never the right answer to this. A Score read from
        # MIDI has PARTS — "bass", "lead" — not channels, and turning one
        # into events without assigning it to a chip first produced an
        # empty event list, a successful render and 0.00 seconds of audio.
        raise ValueError(
            f"{', '.join(sorted(set(unassigned)))} "
            f"{'is' if len(set(unassigned)) == 1 else 'are'} not "
            f"channel{'' if len(set(unassigned)) == 1 else 's'} on any chip "
            f"this engine drives. A score has to be fitted to a target "
            f"before it can be played: arrange.arrange(score, 'YM2612'), "
            f"or `--arrange-for YM2612` on the command line.")

    out = []
    previous = 0
    for row in sorted(rows):
        gap = row - previous
        if gap > 0:
            out.append(E.Wait(ticks=gap * ticks_per_row))
        previous = row
        # Note-offs before note-ons on the same row: a channel that is
        # handed a new note on the row the old one ends must not have the
        # new one silenced by the old one's off.
        events_here = rows[row]
        offs = (E.FMNoteOff, E.OPLNoteOff, E.PSGToneOff, E.PSGNoiseOff,
                E.NESNoteOff, E.NESNoiseOff)
        out.extend(e for e in events_here if isinstance(e, offs))
        out.extend(e for e in events_here if not isinstance(e, offs))
    out.append(E.Wait(ticks=ticks_per_row))
    out.append(E.End())
    return out


def to_json(score: Score) -> dict:
    """A Score as plain data. Used for chips whose notation the tracker
    does not carry — the NES corpus is stored this way rather than as
    .trk, because a .trk is a thing this project can render and an NES
    score is not one yet."""
    return {
        "title": score.title, "bpm": score.bpm, "lpb": score.lpb,
        "bar": score.bar, "rows": score.rows,
        "voices": {name: [[n.row, n.length, n.pitch, n.velocity] for n in v]
                   for name, v in score.voices.items()},
        "drums": score.drums,
    }


def from_json(data: dict, path: str = "") -> Score:
    return Score(
        voices={name: [Note(*row) for row in rows]
                for name, rows in data["voices"].items()},
        drums=data.get("drums", []), bar=data["bar"], lpb=data["lpb"],
        bpm=data["bpm"], rows=data["rows"], title=data.get("title", ""),
        path=path)


def loads(text: str, title: str = "", path: str = "") -> Score:
    """Parse a score from text — tracker notation or Score JSON.

    The text twin of `load`. Going through the event list instead loses
    the title, because a JSON score carries one and an event list has
    nowhere to put it.
    """
    stripped = text.lstrip()
    if stripped.startswith("{"):
        import json
        data = json.loads(text)
        if "voices" in data:
            score = from_json(data, path=path)
            return score._replace(title=title or score.title)
    events, meta = tracker_mod.loads(text)
    return from_events(events, meta, path=path,
                       title=title or getattr(meta, "title", ""))


def load(path: str) -> Score:
    """Read a score. `.trk` is tracker text, `.json` data, `.mid` MIDI."""
    if path.endswith((".mid", ".midi")):
        import midi_import
        return midi_import.load(path).score
    if path.endswith(".json"):
        import json
        with open(path, encoding="utf-8") as handle:
            return from_json(json.load(handle), path=path)
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


#: Directories under corpus/ that hold .json files which are NOT scores.
#: A corpus ships its instrument banks beside its scores, and a bank is
#: a list of patches — load() raises TypeError on one.
_NOT_SCORES = frozenset({"banks"})

#: And the per-corpus metadata, same extension, also not scores.
_METADATA = frozenset({"manifest.json", "profile.json"})


def corpus_paths(root: str = None, suffixes=(".trk", ".json")):
    """Every score in a corpus directory, sorted.

    Banks and metadata are excluded, and that is not cosmetic. This used
    to return every .json under corpus/, which meant 615 instrument
    banks and 3 metadata files on top of the 773 actual scores — a count
    of 1392 where the manifests say 773. Nothing visibly broke, because
    load_all() catches and skips whatever will not parse, but that is
    exactly the problem: with banks in the list, a genuinely malformed
    score being skipped looks identical to a bank being correctly
    ignored.
    """
    root = root or os.path.join(os.path.dirname(_HERE), "corpus")
    found = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _NOT_SCORES]
        for name in sorted(files):
            if name in _METADATA or not name.endswith(tuple(suffixes)):
                continue
            found.append(os.path.join(directory, name))
    return sorted(found)
