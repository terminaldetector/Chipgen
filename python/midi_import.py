"""midi_import.py — read a Standard MIDI File into a Score.

## Why this is here

This project has no way to get notes out of a recording, and it is not
going to pretend otherwise: both transcribers read *register logs*, where
the notes already exist. Turning an MP3 into notes is polyphonic
transcription — a different and much harder problem.

What it can do is accept the answer from whatever solved it. Every
transcriber, every DAW, every notation program in the world writes MIDI,
so MIDI is the join: recording -> some other tool -> .mid -> here ->
`arrange.py` -> a chip. Without this the chain had a gap in the middle
and the engine could only ever play what was written for it.

## What a MIDI file has that a Score does not

**Tempo changes.** A Score carries one BPM. The first `set tempo` wins
and `Import.tempo_changes` counts the rest, because a file that ritards
into the last bar will come out rigid and that should be visible rather
than mysterious.

**Sixteen channels and any number of tracks.** They arrive as voices
named for where they came from; `arrange.py` decides which of them a
chip can hold.

**Free timing.** Notes land wherever a performance put them, and a
tracker row is a grid. Everything is quantised to `lpb` rows a beat, and
`Import.moved` reports the worst distance a note had to travel — a
performance quantised to 4 rows a beat is a different performance, and
a number is the honest way to say by how much.

## What it does not read

Program changes, controllers, pitch bend, aftertouch, key and time
signatures. All of it is skipped rather than half-applied: this reads
notes.
"""

import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import score_model

#: General MIDI puts drums on channel 10 (index 9) whatever the patch
#: says. It is a convention, not a bit in the file, and it is the only
#: way to know a track is percussion without listening to it.
DRUM_CHANNEL = 9

#: A MIDI note number is 12 above this project's pitch units: MIDI 69 and
#: score pitch 57 are both A-4, both 440 Hz.
PITCH_OFFSET = 12

#: 120 BPM, the tempo a file with no `set tempo` event is playing at.
DEFAULT_TEMPO = 500_000          # microseconds per quarter note


class MidiError(ValueError):
    """The file is not a MIDI file, or is one this cannot read."""


class Import:
    """A Score, and what reading it cost."""

    def __init__(self, score, tracks, channels, tempo_changes, moved,
                 dropped, notes):
        self.score = score
        self.tracks = tracks
        self.channels = channels
        self.tempo_changes = tempo_changes
        #: Worst quantisation distance, in rows (0.5 = half a row).
        self.moved = moved
        self.dropped = dropped
        self.notes = notes

    def lines(self) -> list:
        out = [f"{self.score.title or 'untitled'}: {len(self.score.voices)} "
               f"voices from {self.tracks} track"
               f"{'s' if self.tracks != 1 else ''}, "
               f"{sum(len(v) for v in self.score.voices.values())} notes, "
               f"{self.score.bpm:g} BPM"]
        for name, notes in sorted(self.score.voices.items()):
            out.append(f"  {name}: {len(notes)} notes")
        if self.score.drums:
            out.append(f"  drums: {len(self.score.drums)} hits "
                       f"(MIDI channel 10)")
        if self.tempo_changes:
            out.append(f"  note: {self.tempo_changes} tempo change"
                       f"{'s' if self.tempo_changes != 1 else ''} after the "
                       f"first were ignored — a Score carries one BPM, so "
                       f"anything that sped up or slowed down comes out "
                       f"rigid")
        if self.moved > 0.25:
            out.append(f"  note: quantising to {self.score.lpb} rows a beat "
                       f"moved a note by up to {self.moved:.2f} rows — a "
                       f"played performance does not sit on a grid")
        if self.dropped:
            out.append(f"  note: {self.dropped} note"
                       f"{'s' if self.dropped != 1 else ''} were shorter "
                       f"than one row and would have vanished; each was "
                       f"kept at one row")
        out.extend(f"  note: {n}" for n in self.notes)
        return out

    def __str__(self):
        return "\n".join(self.lines())


def _varint(data, index):
    """MIDI's variable-length quantity: seven bits a byte, high bit = more."""
    value = 0
    for _ in range(4):
        if index >= len(data):
            raise MidiError("file ends in the middle of a delta time")
        byte = data[index]
        index += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, index
    raise MidiError("a variable-length quantity longer than four bytes")


def _chunks(data):
    """Every (type, payload) chunk, in order."""
    index = 0
    while index + 8 <= len(data):
        kind = data[index:index + 4]
        (length,) = struct.unpack(">I", data[index + 4:index + 8])
        body = data[index + 8:index + 8 + length]
        if len(body) < length:
            # A truncated final track is common in files a converter cut
            # short; what was read is still music.
            yield kind, body
            return
        yield kind, body
        index += 8 + length


def _events(track):
    """(absolute tick, status, data...) for every event in one track."""
    out = []
    index = 0
    tick = 0
    status = None
    while index < len(track):
        delta, index = _varint(track, index)
        tick += delta
        if index >= len(track):
            break
        byte = track[index]
        if byte & 0x80:
            status = byte
            index += 1
        elif status is None:
            raise MidiError("a data byte before any status byte")
        # else: running status — the previous status, new data.

        if status == 0xFF:                      # meta
            if index + 1 > len(track):
                break
            kind = track[index]
            index += 1
            length, index = _varint(track, index)
            out.append((tick, 0xFF, kind, track[index:index + length]))
            index += length
        elif status in (0xF0, 0xF7):            # sysex, skipped whole
            length, index = _varint(track, index)
            index += length
        else:
            high = status & 0xF0
            width = 1 if high in (0xC0, 0xD0) else 2
            out.append((tick, status, track[index], 
                        track[index + 1] if width == 2
                        and index + 1 < len(track) else 0))
            index += width
    return out


def read(data: bytes, lpb: int = 4, title: str = "") -> Import:
    """Parse MIDI bytes into a Score. `lpb` is rows per beat."""
    chunks = list(_chunks(data))
    if not chunks or chunks[0][0] != b"MThd":
        raise MidiError("not a MIDI file — no MThd header chunk")
    if len(chunks[0][1]) < 6:
        raise MidiError("the MThd header is too short to read")
    _format, _ntrks, division = struct.unpack(">HHh", chunks[0][1][:6])
    if division <= 0:
        # SMPTE timing: frames per second in the high byte, ticks per
        # frame in the low one. Refused rather than guessed — the whole
        # row grid hangs off this number.
        raise MidiError(
            "SMPTE timing (a negative division) is not supported; this "
            "reads ticks-per-quarter-note files, which is what every "
            "transcriber and DAW writes by default")

    tracks = [body for kind, body in chunks[1:] if kind == b"MTrk"]
    if not tracks:
        raise MidiError("no MTrk track chunks in the file")

    tempo = None
    tempo_changes = 0
    names = {}
    file_title = title
    merged = []
    for number, track in enumerate(tracks):
        for event in _events(track):
            tick, status = event[0], event[1]
            if status == 0xFF:
                kind, payload = event[2], event[3]
                if kind == 0x51 and len(payload) == 3:
                    value = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                    if tempo is None:
                        tempo = value
                    elif value != tempo:
                        tempo_changes += 1
                elif kind == 0x03 and payload:
                    text = payload.decode("latin-1", "replace").strip()
                    if number == 0 and not file_title:
                        file_title = text
                    elif text:
                        names[number] = text
                continue
            merged.append((tick, number, status, event[2], event[3]))

    tempo = tempo or DEFAULT_TEMPO
    bpm = 60_000_000.0 / tempo
    ticks_per_row = division / float(lpb)

    # Sort by tick, then put note-offs before note-ons so a repeated note
    # on one channel closes before it reopens.
    merged.sort(key=lambda e: (e[0], 0 if _is_off(e) else 1))

    open_notes = {}          # (track, channel, pitch) -> (row, velocity, tick)
    voices = {}
    drums = []
    moved = 0.0
    dropped = 0
    last_row = 0

    def place(key, end_tick):
        nonlocal dropped, last_row
        start = open_notes.pop(key, None)
        if start is None:
            return
        row, velocity, start_tick = start
        end = int(round(end_tick / ticks_per_row))
        length = end - row
        if length < 1:
            length = 1
            dropped += 1
        voice = _voice_name(key[0], key[1], names)
        voices.setdefault(voice, []).append(score_model.Note(
            row=row, length=length,
            pitch=key[2] - PITCH_OFFSET, velocity=velocity))
        last_row = max(last_row, row + length)

    for tick, number, status, data1, data2 in merged:
        high = status & 0xF0
        channel = status & 0x0F
        if high not in (0x80, 0x90):
            continue
        key = (number, channel, data1)
        if high == 0x80 or data2 == 0:
            place(key, tick)
            continue

        exact = tick / ticks_per_row
        row = int(round(exact))
        moved = max(moved, abs(exact - row))
        if channel == DRUM_CHANNEL:
            drums.append(row)
            last_row = max(last_row, row + 1)
            continue
        place(key, tick)                   # a re-key closes the old note
        open_notes[key] = (row, max(1, min(127, data2)), tick)

    final = max((e[0] for e in merged), default=0)
    for key in list(open_notes):
        place(key, final + ticks_per_row)

    drums = sorted(set(drums))
    score = score_model.Score(
        voices={name: sorted(notes, key=lambda n: (n.row, n.pitch))
                for name, notes in voices.items()},
        drums=drums, bar=max(1, lpb * 4), lpb=lpb, bpm=round(bpm, 3),
        rows=last_row, title=file_title, path="")

    notes = []
    if drums:
        notes.append(f"MIDI channel 10 is percussion by convention, so its "
                     f"{len(drums)} hits became the drum track rather than "
                     f"a voice")
    return Import(score, len(tracks), len({v for v in voices}),
                  tempo_changes, moved, dropped, notes)


def _is_off(event) -> bool:
    status, data2 = event[2], event[4]
    return (status & 0xF0) == 0x80 or ((status & 0xF0) == 0x90 and data2 == 0)


def _voice_name(track: int, channel: int, names: dict) -> str:
    """What to call a MIDI part in a Score.

    The track's own name when it has one, because "Bass" placed by the
    role classifier beats "t1c2" every time; the numbers otherwise.
    """
    label = names.get(track)
    if label:
        clean = "".join(c if c.isalnum() else "_" for c in label.lower())
        clean = clean.strip("_")
        if clean:
            return clean
    return f"ch{channel + 1}"


def load(path: str, lpb: int = 4) -> Import:
    with open(path, "rb") as handle:
        data = handle.read()
    title = os.path.splitext(os.path.basename(path))[0]
    result = read(data, lpb=lpb, title="")
    if not result.score.title:
        result.score = result.score._replace(title=title)
    result.score = result.score._replace(path=path)
    return result


def main(argv):
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="read a MIDI file into a chipgen score")
    parser.add_argument("source", help="a .mid file")
    parser.add_argument("--lpb", type=int, default=4,
                        help="rows per beat to quantise onto (default 4)")
    parser.add_argument("--for", dest="target",
                        help="also fit it onto a chip: RP2A03, YM2612, "
                             "YM3812")
    parser.add_argument("-o", "--out", help="write the score here (.trk)")
    args = parser.parse_args(argv)

    result = load(args.source, lpb=args.lpb)
    print(result)
    score = result.score

    if args.target:
        import arrange
        print()
        score, report = arrange.arrange(score, args.target)
        print(report)

    if args.out:
        import tracker
        meta = tracker.Metadata()
        meta.bpm, meta.lpb = score.bpm, score.lpb
        meta.title = score.title
        if args.out.endswith(".json"):
            with open(args.out, "w", encoding="utf-8") as handle:
                json.dump(score_model.to_json(score), handle, indent=1)
        else:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(tracker.dumps(score_model.to_events(score),
                                           meta))
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
