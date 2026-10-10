"""nes_transcribe.py — turn an NES VGM register log back into notes.

Same job as vgm_transcribe.py does for the Genesis, and the same caveat:
a register log is what the driver wrote, not what the composer meant, and
some of what the composer meant is not recoverable. What comes out is
notes with pitch, length and velocity per channel. What does not come out
is which drum a noise burst was, or the difference between a rest and a
note the driver forgot to stop.

Three things are specific to this chip and each one changes the result:

**Arpeggios are not ornaments here, they are chords.** With two pulse
channels and a triangle that cannot change volume, a three-note chord is
played by rewriting one pulse's period every frame. A hold rule tuned to
throw away fast pitch changes — which is the right rule for Genesis PSG
vibrato — throws away the harmony on this platform. The threshold below
is measured against the dump rather than carried over.

**The triangle has no velocity.** It is on or off. Every triangle note
comes out at the same velocity because that is all the hardware can say.

**Percussion comes out badly and that is not fixed.** A drum on this chip
is the noise channel re-triggered at the SAME period — the hit is the
envelope restarting, not a pitch change — and this transcriber emits a
note when the pitch changes. Measured over the dump, the noise channel
appears in 32 of 158 accepted tracks when in reality nearly all of them
have drums. Treat the noise track as unreliable: the pitched channels are
what this corpus is for.

**A note ends when the length counter runs out**, which the log does not
announce. Note-off is inferred from the channel going silent — volume to
zero, enable bit cleared, or the length counter expiring on its own — so
lengths are as good as that inference and no better.
"""

import collections
import math
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import nes_apu
import score_model
import vgm
from vgm_transcribe import Note, frequency_to_note, infer_grid

VGM_SAMPLE_RATE = 44100.0

#: Channel names in the Score. Descriptive rather than numbered, because
#: on this chip the channel IS the role far more than it is on the YM2612.
PULSE1, PULSE2, TRIANGLE, NOISE = "pulse1", "pulse2", "triangle", "noise"

#: A pitch change that holds less than this AND moves by no more than
#: SLIDE_INTERVAL semitones is a pitch effect, not a note.
#:
#: Duration alone cannot make this call on the NES, which is why the rule
#: has two halves. With two pulses and a triangle that cannot change
#: volume, a chord is played by rewriting one channel's period every
#: frame — so a one-frame pitch change is sometimes a real chord tone.
#: The Genesis PSG's 45 ms rule applied here discards 52.4% of every pitch
#: change in the dump, and 20 ms still discards 26.9%: the median run is
#: 33 ms, exactly two frames, and the lower quartile is one frame.
#:
#: What separates the two populations is interval, not time. Measured over
#: 60 tracks, runs of one frame or less are 77-84% single-semitone moves,
#: while runs longer than that spread across one to five semitones. A
#: one-frame semitone step is a slide or vibrato crossing a boundary; a
#: one-frame jump of a third or more is an arpeggio doing the harmony.
MIN_HOLD = 0.025
#: Moves this small, held briefly, are the pitch effect rather than a note.
SLIDE_INTERVAL = 2

#: Smallest gap we will place between two notes on one voice, in seconds.
#: Well under a frame, so it never moves a note to a different row.
_TIE_BREAK = 1e-4

#: Consecutive small steps in the SAME direction, closer together than
#: this, are one slide rather than a run of notes.
#:
#: This is what the triangle is doing when a driver uses it for percussion,
#: which is common because the noise channel alone cannot carry a kit: a
#: kick is a fast chromatic sweep downward. Measured, the sweep steps sit
#: about 33 ms apart — above the slide threshold above, so each step was
#: emitting a note and one drum hit became eight. Before this rule the
#: triangle held 56% of every note in the dump and outnumbered each pulse
#: three to one, which is backwards for a bass line.
SLIDE_WINDOW = 0.060

#: A pulse period below 8 is muted by the sweep unit; the triangle needs
#: at least 2 or it runs into ultrasound. Both are hardware, not policy.
MIN_PULSE_PERIOD = 8
MIN_TRIANGLE_PERIOD = 2


class _Channel:
    """Tracks one channel's audibility and emits notes when it changes."""

    def __init__(self, name, clock, divider):
        self.name = name
        self.clock = clock
        self.divider = divider          # 16 for pulses, 32 for the triangle
        self.period = 0
        self.volume = 15
        self.enabled = False
        self.length = 0
        self.floor = (MIN_TRIANGLE_PERIOD if name == TRIANGLE
                      else MIN_PULSE_PERIOD)
        self.sounding = None            # semitone currently emitted
        self.since = 0.0
        self.pending = None
        self.pending_since = 0.0
        self.direction = 0            # sign of the last accepted move
        self.moved_at = 0.0
        self.notes = []

    def frequency(self):
        if self.period < self.floor:
            return 0.0
        return self.clock / (self.divider * (self.period + 1))

    def audible(self):
        return (self.enabled and self.length > 0 and self.period >= self.floor
                and (self.name == TRIANGLE or self.volume > 0))

    def velocity(self):
        if self.name == TRIANGLE:
            return 127                  # no volume control exists
        return max(1, min(127, int(round(self.volume * 127 / 15.0))))

    def update(self, now):
        """Called after any register write that could change audibility."""
        if not self.audible():
            self._close(now)
            self.pending = None
            return
        name, octave, cents = frequency_to_note(self.frequency())
        if name is None:
            self._close(now)
            return
        import events as events_mod
        semitone = octave * 12 + events_mod.NOTE_NAMES.index(name)
        if semitone == self.sounding:
            self.pending = None
            return
        if self._is_slide(semitone, now):
            # Same direction, small step, arriving fast: extend the note
            # that is already sounding rather than starting another.
            self.sounding = semitone
            self.moved_at = now
            self.pending = None
            return
        if self.pending is not None and self.pending[0] == semitone:
            if now - self.pending_since >= MIN_HOLD or self._is_leap(semitone):
                self._open(self.pending_since, semitone, name, octave, cents)
                self.pending = None
            return
        self.pending = (semitone, name, octave, cents)
        self.pending_since = now
        # A big jump does not have to wait: it is an arpeggio step, and
        # holding it for the slide threshold would drop the chord.
        if self._is_leap(semitone):
            self._open(now, semitone, name, octave, cents)
            self.pending = None

    def _is_leap(self, semitone) -> bool:
        return (self.sounding is not None
                and abs(semitone - self.sounding) > SLIDE_INTERVAL)

    def _is_slide(self, semitone, now) -> bool:
        if self.sounding is None:
            return False
        step = semitone - self.sounding
        if step == 0 or abs(step) > SLIDE_INTERVAL:
            return False
        if now - self.moved_at > SLIDE_WINDOW:
            return False
        return self.direction != 0 and (step > 0) == (self.direction > 0)

    def flush(self, now):
        """A pending pitch that held to the end of the log is a real note."""
        if self.pending is not None and now - self.pending_since >= MIN_HOLD:
            semitone, name, octave, cents = self.pending
            self._open(self.pending_since, semitone, name, octave, cents)
            self.pending = None
        self._close(now)

    def _open(self, when, semitone, name, octave, cents):
        # A pending pitch is timestamped when it first appeared, which can
        # be at or before the note currently sounding started. Opening
        # there would put two notes on one voice at the same instant —
        # something one channel cannot do, and which the grid then
        # quantises into an impossible chord.
        if self.sounding is not None and when <= self.since:
            when = self.since + _TIE_BREAK
        if self.sounding is not None:
            self.notes.append(Note(when, self.name, "off"))
        self.notes.append(Note(when, self.name, "on", name, octave,
                               self.velocity(), cents))
        self.direction = (0 if self.sounding is None
                          else (1 if semitone > self.sounding else -1))
        self.sounding = semitone
        self.since = when
        self.moved_at = when

    def _close(self, now):
        if self.sounding is not None:
            self.notes.append(Note(now, self.name, "off"))
            self.sounding = None
            self.direction = 0


def _walk(raw, max_seconds):
    """Yield (time, address, value) per APU write, then (time, None, None).

    The final yield carries the log's END time, which is not the time of
    the last write: a jingle whose closing chord rings out writes nothing
    for the last second. Reporting the last write as the duration made 24
    of 214 tracks come back about a second short, all of them short cues.
    """
    header = vgm.read_header(raw)
    pos = header["data_offset"]
    samples = 0
    limit = max_seconds * VGM_SAMPLE_RATE
    end = min(len(raw), header["eof_offset"] or len(raw))
    while pos < end and samples < limit:
        command = raw[pos]
        if command == vgm.CMD_NES_APU:
            yield samples / VGM_SAMPLE_RATE, 0x4000 | raw[pos + 1], raw[pos + 2]
            pos += 3
        elif command == vgm.CMD_END:
            break
        elif command == vgm.CMD_WAIT_LONG:
            samples += struct.unpack_from("<H", raw, pos + 1)[0]
            pos += 3
        elif command == vgm.CMD_WAIT_735:
            samples += 735
            pos += 1
        elif command == vgm.CMD_WAIT_882:
            samples += 882
            pos += 1
        elif 0x70 <= command <= 0x7F:
            samples += (command & 0x0F) + 1
            pos += 1
        elif 0x80 <= command <= 0x8F:
            samples += command & 0x0F
            pos += 1
        elif command == vgm.CMD_DATA_BLOCK:
            size = struct.unpack_from("<I", raw, pos + 3)[0]
            pos += 7 + size
        elif command in (0x50, 0x30):
            pos += 2
        elif command in (0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58,
                         0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0xA0,
                         0xB0, 0xB1, 0xB2, 0xB3, 0xB5, 0xB6, 0xB7, 0xB8,
                         0xB9, 0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF):
            pos += 3
        elif 0xC0 <= command <= 0xDF:
            pos += 4
        elif 0xE0 <= command <= 0xFF:
            pos += 5
        elif command == 0x66:
            break
        else:
            pos += 1
    yield min(samples, limit) / VGM_SAMPLE_RATE, None, None


def transcribe(path_or_bytes, max_seconds: float = 600.0):
    """-> (notes, info). `notes` is a flat time-ordered list of Note."""
    raw = vgm._raw_bytes(path_or_bytes)
    header = vgm.read_header(raw)
    clock = header.get("nes_clock") or nes_apu.NTSC_CPU_CLOCK

    channels = {
        PULSE1: _Channel(PULSE1, clock, 16),
        PULSE2: _Channel(PULSE2, clock, 16),
        TRIANGLE: _Channel(TRIANGLE, clock, 32),
    }
    noise = _Channel(NOISE, clock, 16)
    noise.floor = 0
    channels[NOISE] = noise

    now = 0.0
    pending_time = None
    for now, address, value in _walk(raw, max_seconds):
        # Settle the channels only once per timestamp, after every write
        # that shares it. A note needs two or three register writes — low
        # period byte, high byte, volume — and looking at the channel
        # between them reads a half-written note. Measured, this emitted
        # two different notes at the same instant on one channel, which is
        # not something one voice can do.
        if pending_time is not None and now > pending_time:
            for channel in channels.values():
                channel.update(pending_time)
        if address is None:
            break                      # end marker: `now` is the log's length
        _apply(channels, address, value)
        pending_time = now
    if pending_time is not None:
        for channel in channels.values():
            channel.update(pending_time)
    for channel in channels.values():
        channel.flush(now)

    notes = sorted((n for c in channels.values() for n in c.notes),
                   key=lambda n: (n.time, n.channel))
    info = {
        "duration": now,
        "clock": clock,
        "chips": vgm.detect_chips(raw),
        "notes": sum(1 for n in notes if n.kind == "on"),
    }
    return notes, info


def _apply(channels, address, value):
    pulse = None
    if 0x4000 <= address <= 0x4003:
        pulse, index = channels[PULSE1], address & 3
    elif 0x4004 <= address <= 0x4007:
        pulse, index = channels[PULSE2], address & 3
    if pulse is not None:
        if index == 0:
            # Constant volume in the low nibble; in envelope mode the same
            # nibble is the decay RATE, and the level starts at 15. Reading
            # it as a level either way would report a fast envelope as a
            # quiet note.
            pulse.volume = (value & 0x0F) if (value & 0x10) else 15
        elif index == 2:
            pulse.period = (pulse.period & 0x700) | value
        elif index == 3:
            pulse.period = (pulse.period & 0xFF) | ((value & 7) << 8)
            pulse.length = nes_apu.LENGTH_TABLE[(value >> 3) & 31]
        return

    triangle = channels[TRIANGLE]
    if address == 0x400A:
        triangle.period = (triangle.period & 0x700) | value
    elif address == 0x400B:
        triangle.period = (triangle.period & 0xFF) | ((value & 7) << 8)
        triangle.length = nes_apu.LENGTH_TABLE[(value >> 3) & 31]
    elif address == 0x400C:
        channels[NOISE].volume = (value & 0x0F) if (value & 0x10) else 15
    elif address == 0x400E:
        channels[NOISE].period = value & 0x0F
    elif address == 0x400F:
        channels[NOISE].length = nes_apu.LENGTH_TABLE[(value >> 3) & 31]
    elif address == 0x4015:
        for bit, name in ((0x01, PULSE1), (0x02, PULSE2), (0x04, TRIANGLE),
                          (0x08, NOISE)):
            channel = channels[name]
            channel.enabled = bool(value & bit)
            if not channel.enabled:
                channel.length = 0


def to_score(notes, info, title: str = "", path: str = "",
             lpb: int = None) -> score_model.Score:
    """Quantise a note list onto the grid it was played on. -> Score."""
    row_seconds, bpm, grid_lpb, coverage = infer_grid(notes, lpb)
    if lpb is None and row_seconds:
        # Halve the row. infer_grid resolves the factor-of-two ambiguity by
        # preferring the LONGEST well-scoring period, which is right for the
        # Genesis corpus it was written against and one step too coarse
        # here: measured over the dump, the median gap between consecutive
        # notes on one NES voice is 0.51 rows, so half the notes were
        # landing on a row that already had one and being dropped. At half
        # the row length that gap is 1.02 rows — one note per row, which is
        # what a row is for — and quantisation keeps 69% of note-ons
        # instead of 51%.
        row_seconds /= 2.0
        grid_lpb = (grid_lpb or 4) * 2
    voices = collections.defaultdict(list)
    open_notes = {}
    last_row = 0

    def place(channel, begin, row, pitch, velocity):
        """Add a note, replacing any that starts on the same row.

        Two register writes a few milliseconds apart are two notes in the
        log and one note on the grid, because a row here is around 100 ms.
        One channel plays one note, so the later write is the one that
        sounds and the earlier one never really did.
        """
        note = score_model.Note(row=begin, length=max(1, row - begin),
                                pitch=pitch, velocity=velocity)
        bucket = voices[channel]
        if bucket and bucket[-1].row == begin:
            bucket[-1] = note
        else:
            bucket.append(note)

    for note in notes:
        row = int(round(note.time / row_seconds))
        last_row = max(last_row, row)
        if note.kind == "off":
            start = open_notes.pop(note.channel, None)
            if start is not None:
                place(note.channel, start[0], row, start[1], start[2])
            continue
        start = open_notes.pop(note.channel, None)
        if start is not None:
            place(note.channel, start[0], row, start[1], start[2])
        import events as events_mod
        pitch = note.octave * 12 + events_mod.NOTE_NAMES.index(note.note)
        open_notes[note.channel] = (row, pitch, note.velocity)
    for channel, (begin, pitch, velocity) in open_notes.items():
        place(channel, begin, last_row + 1, pitch, velocity)

    lpb_used = grid_lpb or 4
    return score_model.Score(
        voices={name: sorted(v, key=lambda n: (n.row, n.pitch))
                for name, v in voices.items()},
        drums=[n.row for n in voices.get(NOISE, [])],
        bar=max(1, lpb_used * 4), lpb=lpb_used, bpm=bpm,
        rows=last_row, title=title, path=path)


def transcribe_score(path: str, max_seconds: float = 600.0,
                     lpb: int = None) -> score_model.Score:
    notes, info = transcribe(path, max_seconds)
    title = os.path.splitext(os.path.basename(path))[0]
    return to_score(notes, info, title=title, path=path, lpb=lpb)


def measure_hold(paths, limit: int = 40):
    """How long pitch runs actually last, so MIN_HOLD can be set not guessed."""
    runs = []
    for path in paths[:limit]:
        try:
            notes, _ = transcribe(path, max_seconds=90.0)
        except Exception:
            continue
        last = {}
        for note in notes:
            if note.kind == "on":
                if note.channel in last:
                    runs.append(note.time - last[note.channel])
                last[note.channel] = note.time
    runs.sort()
    return runs


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------
#: A track needs this many notes before a grid can be inferred at all.
MIN_NOTES = 24
#: And this much of its onsets near a row line before the transcription is
#: worth keeping. Measured over the dump, tracks that pass sit around 0.47
#: median coverage against the Genesis corpus's 0.62 — NES drivers run
#: their own tempo off a 60 Hz frame counter and land less squarely on a
#: single uniform grid.
MIN_COVERAGE = 0.30


#: Serialisation lives in score_model so the loader can dispatch on it.
score_to_json = staticmethod(score_model.to_json) if False else score_model.to_json
score_from_json = score_model.from_json


def build_corpus(paths, out_dir: str, max_seconds: float = 240.0,
                 progress=None):
    """Transcribe many NES VGMs into a corpus directory. -> report dict."""
    import json
    from vgm_transcribe import infer_grid
    os.makedirs(out_dir, exist_ok=True)
    kept, dropped = [], []
    for i, path in enumerate(paths, 1):
        if progress:
            progress(i, len(paths), path)
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            chips = vgm.detect_chips(path)
            extra = [c for c in chips if c not in vgm.SUPPORTED_CHIPS]
            if extra:
                dropped.append((name, "needs " + ", ".join(extra)))
                continue
            notes, info = transcribe(path, max_seconds)
        except Exception as error:
            dropped.append((name, f"unreadable: {error}"))
            continue
        played = sum(1 for n in notes if n.kind == "on")
        if played < MIN_NOTES:
            dropped.append((name, f"only {played} notes"))
            continue
        coverage = infer_grid(notes)[3]
        if coverage < MIN_COVERAGE:
            dropped.append((name, f"grid coverage {coverage:.2f}"))
            continue
        score = to_score(notes, info, title=name, path=path)
        game = os.path.basename(os.path.dirname(path))
        out = os.path.join(out_dir, f"{_slug(game)}__{_slug(name)}.json")
        payload = score_to_json(score)
        # Store the game rather than recovering it from the filename: the
        # slug flattens punctuation, so "Batman - Return of the Joker"
        # and "Batman" both start "batman__" and get counted as one game.
        payload["game"] = game
        payload["source"] = os.path.basename(path)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        kept.append((out, played, coverage))
    return {"kept": kept, "dropped": dropped,
            "notes": sum(k[1] for k in kept)}


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in text]
    return "".join(keep).strip("_")[:60]
