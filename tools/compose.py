"""compose.py — the small kit the synthetic demos are written with.

The demos are generated, not recorded and not typed: a Python script says
"a four-bar pattern in A minor, kick on the beat, bass on these rows" and
this kit turns that into the text a model would write. The text is what is
committed (demos/*.sch) and what the build compiles, so the generator is a
convenience for making many tracks, not a part of the format.
"""

import random

# -- notes -------------------------------------------------------------------
NAMES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")
MODES = {
    "major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10), "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11), "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "pentatonic": (0, 2, 4, 7, 9), "minor_pentatonic": (0, 3, 5, 7, 10),
}


def midi(name: str) -> int:
    """'A-4' -> 57 (semitones above C-0)."""
    return NAMES.index(name[:2]) + 12 * int(name[2])


def name(semitones: int) -> str:
    return f"{NAMES[semitones % 12]}{semitones // 12}"


def scale_note(root: str, mode: str, degree: int) -> int:
    """Semitones for scale `degree` (0 = root, 7 = root an octave up, -1 =
    the note below the root...)."""
    steps = MODES[mode]
    octave, index = divmod(degree, len(steps))
    return midi(root) + 12 * octave + steps[index]


def triad(root: str, mode: str, degree: int, inversion: int = 0):
    """Semitones of the triad built on a scale degree."""
    notes = [scale_note(root, mode, degree + k) for k in (0, 2, 4)]
    for _ in range(inversion):
        notes = notes[1:] + [notes[0] + 12]
    return notes


def euclid(pulses: int, steps: int, rotate: int = 0):
    """Bjorklund: `pulses` hits spread as evenly as possible over `steps`."""
    if pulses <= 0:
        return [False] * steps
    if pulses >= steps:
        return [True] * steps
    pattern = []
    bucket = 0
    for _ in range(steps):
        bucket += pulses
        if bucket >= steps:
            bucket -= steps
            pattern.append(True)
        else:
            pattern.append(False)
    rotate %= steps
    return pattern[rotate:] + pattern[:rotate]


# -- patterns ----------------------------------------------------------------
EMPTY = "... .. ... ..."


def cell(note="...", ins="..", vol="...", fx="..."):
    if isinstance(note, int):
        note = name(note)
    if isinstance(ins, int):
        ins = f"{ins:02d}"
    return f"{note} {ins} {vol} {fx if fx != '..' else '...'}"


class Pattern:
    def __init__(self, name: str, rows: int, channels: int):
        self.name, self.rows, self.channels = name, rows, channels
        self.grid = [[EMPTY] * channels for _ in range(rows)]

    def put(self, row: int, channel: int, note="...", ins="..", vol="...",
            fx="..."):
        """`channel` counts from 1."""
        if 0 <= row < self.rows:
            self.grid[row][channel - 1] = cell(note, ins, vol, fx)

    def text(self) -> str:
        body = "\n".join(" | ".join(r) for r in self.grid)
        return f"pattern {self.name} rows {self.rows}\n{body}\nend\n"


def header(title, bpm, channels, extra=()):
    lines = [f"title {title}", f"tempo {bpm}", f"channels {channels}"]
    lines += list(extra)
    return "\n".join(lines) + "\n"
