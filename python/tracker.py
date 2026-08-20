r"""
tracker.py — a compact text notation that parses to and from events.

The JSON event list is the machine contract, and it is verbose: a 4-bar
pattern runs to several hundred objects and a few thousand tokens. A model
paying by the token spends most of its budget on punctuation, and a human
reading it cannot see the music. Trackers solved this in 1987 — a grid,
one row per step, one column per voice — and the shape has not been
improved on since.

So the same pattern in tracker notation:

    bpm 172
    lpb 4
    inst fm0 bass
    inst fm1 distorted_lead
    cols fm0 fm1 psg0 noise dac

    A-2  ...  A-4  w1   kick
    ...  ...  C-5  ...  hat
    C-3  E-4  E-5  w1   snare
    ...  ...  A-5  ...  hat

Same music, a fifth of the tokens, and you can see the groove. It is a
view of the event list, not a replacement: `loads()` gives you events,
`dumps()` gives you text, and both directions run in the tests.

## Grammar

Directives (anywhere, one per line; a line whose first word is a
directive keyword is a directive):

    bpm 172              tempo
    lpb 4                rows per beat (4 = sixteenth notes)
    ticks 192            sequencer tick rate, if you need to change it
    inst fm0 bass        assign a patch to an FM channel
    vol fm0 100          channel volume (FM 0-127, PSG 0-15)
    pan fm1 L            L | R | C | off, optionally: pan fm1 C 2 3 (ams pms)
    lfo on 4             global LFO on at rate 0-7 / `lfo off`
    pitch fm1 -12        detune the channel in cents
    cols fm0 fm1 psg0    which columns the rows below carry
    loop                 mark the VGM loop point here
    title / author / game / notes    GD3 metadata for the .vgm
    end                  stop early

Row cells, by column type:

    fm0..fm5    A-2  A#3  A-2:100 (velocity 1-127)  ===/off (note off)  .../-- (hold)
    psg0..psg2  A-4  A-4:8 (volume 0-15, 0 loudest)  ===/off  .../--
    noise       w0..w3 (white) p0..p3 (periodic)     ===/off  .../--
    dac         kick snare hat hat_open tom clap rim  .../--

Comments run to end of line: `;` anywhere, `#` at the start of a line or
after whitespace (so the sharp in `A#2` is safe). Blank lines are ignored,
so group rows into bars however you like.
"""

import re

import events as events_mod
from events import (DACSample, End, FMInstrumentSelect, FMLFO, FMNoteOff,
                    FMNoteOn, FMPan, FMPitch, FMVolume, LoopPoint,
                    PSGNoiseOff, PSGNoiseOn, PSGToneOff, PSGToneOn,
                    PSGVolume, Tempo, Wait)

DEFAULT_BPM = 150.0
DEFAULT_LPB = 4
DEFAULT_TICKS_PER_SECOND = 192.0

HOLD_TOKENS = {"...", "..", ".", "--", "---", "-", "~", ""}
OFF_TOKENS = {"===", "==", "off", "^^^", "^"}

_FM_COLUMNS = tuple(f"fm{i}" for i in range(6))
_PSG_COLUMNS = tuple(f"psg{i}" for i in range(3))
_COLUMN_ALIASES = {}
for _i in range(6):
    _COLUMN_ALIASES[f"f{_i}"] = f"fm{_i}"
for _i in range(3):
    _COLUMN_ALIASES[f"p{_i}"] = f"psg{_i}"
    _COLUMN_ALIASES[f"psg{_i}"] = f"psg{_i}"
_COLUMN_ALIASES.update({"n": "noise", "ns": "noise", "noise": "noise",
                        "d": "dac", "pcm": "dac", "dac": "dac"})

DEFAULT_COLUMNS = ("fm0", "fm1", "fm2", "psg0", "noise", "dac")

DIRECTIVES = {"bpm", "lpb", "ticks", "inst", "vol", "pan", "lfo", "pitch",
              "cols", "columns", "loop", "title", "author", "game", "notes",
              "end"}

_NOTE_CELL = re.compile(r"^([A-Ga-g])([#b\-]?)(-?\d)(?::(\d+))?$")
#: `;` always starts a comment; `#` only at line start or after whitespace,
#: so that the sharp in `A#2` survives.
_COMMENT = re.compile(r"(?:(?:^|(?<=\s))#|;)")
_NOISE_CELL = re.compile(r"^([wpWP])([0-3])(?::(\d+))?$")


class TrackerError(ValueError):
    """Raised with a line number, because a grid without one is unsearchable."""


class Metadata:
    __slots__ = ("title", "author", "game", "notes", "bpm", "lpb",
                 "ticks_per_second")

    def __init__(self):
        self.title = ""
        self.author = ""
        self.game = ""
        self.notes = ""
        self.bpm = DEFAULT_BPM
        self.lpb = DEFAULT_LPB
        self.ticks_per_second = DEFAULT_TICKS_PER_SECOND

    def ticks_per_row(self) -> int:
        seconds = 60.0 / self.bpm / self.lpb
        return max(1, round(seconds * self.ticks_per_second))

    def to_gd3(self):
        import vgm
        return vgm.GD3(title=self.title, author=self.author, game=self.game,
                       notes=self.notes)

    def __repr__(self):
        return (f"Metadata(bpm={self.bpm}, lpb={self.lpb}, "
                f"ticks_per_row={self.ticks_per_row()})")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def parse_note(cell: str):
    """'A-2' / 'A#3' / 'Bb4' -> (note, octave, param or None)."""
    m = _NOTE_CELL.match(cell)
    if not m:
        return None
    letter, accidental, octave, param = m.groups()
    name = letter.upper()
    if accidental == "#":
        name += "#"
    elif accidental == "b":
        name += "b"
    canonical = events_mod.normalize_note(name)
    if canonical is None:
        return None
    return canonical, int(octave), (int(param) if param is not None else None)


def loads(text: str):
    """Parse tracker text. Returns (events, metadata)."""
    meta = Metadata()
    columns = list(DEFAULT_COLUMNS)
    events = []

    pending_rows = 0          # rows of silence not yet emitted as a Wait
    fm_sounding = [False] * 6
    psg_sounding = [False] * 3
    noise_sounding = False
    stopped = False

    def flush_rows():
        nonlocal pending_rows
        if pending_rows:
            events.append(Wait(ticks=pending_rows * meta.ticks_per_row()))
            pending_rows = 0

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _COMMENT.split(raw, maxsplit=1)[0].strip()
        if not line or stopped:
            continue

        words = line.split()
        head = words[0].lower()

        if head in DIRECTIVES:
            flush_rows()
            stopped = _directive(head, words[1:], meta, columns, events, lineno)
            continue

        cells = [c for c in line.replace("|", " ").split() if c]
        if len(cells) != len(columns):
            raise TrackerError(
                f"line {lineno}: {len(cells)} cells but {len(columns)} columns "
                f"({' '.join(columns)}). Use ... for an empty cell.\n  {raw.strip()}")

        flush_rows()
        for column, cell in zip(columns, cells):
            _apply_cell(column, cell, events, fm_sounding, psg_sounding,
                        lineno, raw)
            if column == "noise":
                noise_sounding = _noise_state(cell, noise_sounding)
        pending_rows = 1

    flush_rows()

    # Release anything still held, so a pattern never ends on a stuck note.
    for ch in range(6):
        if fm_sounding[ch]:
            events.append(FMNoteOff(channel=ch))
    for ch in range(3):
        if psg_sounding[ch]:
            events.append(PSGToneOff(channel=ch))
    if noise_sounding:
        events.append(PSGNoiseOff())
    events.append(End())
    return events, meta


def _noise_state(cell: str, current: bool) -> bool:
    token = cell.strip()
    if token in HOLD_TOKENS:
        return current
    if token.lower() in OFF_TOKENS:
        return False
    return bool(_NOISE_CELL.match(token))


def _directive(head, args, meta, columns, events, lineno) -> bool:
    """Apply one directive. Returns True if it ends the score."""
    def need(n, usage):
        if len(args) < n:
            raise TrackerError(f"line {lineno}: {head} needs {usage}")

    if head == "bpm":
        need(1, "a tempo, e.g. `bpm 172`")
        meta.bpm = float(args[0])
    elif head == "lpb":
        need(1, "rows per beat, e.g. `lpb 4`")
        meta.lpb = max(1, int(args[0]))
    elif head == "ticks":
        need(1, "a tick rate, e.g. `ticks 192`")
        meta.ticks_per_second = float(args[0])
        events.append(Tempo(ticks_per_second=meta.ticks_per_second))
    elif head in ("cols", "columns"):
        need(1, "at least one column name")
        columns[:] = [_column(a, lineno) for a in args]
    elif head == "inst":
        need(2, "a channel and an instrument, e.g. `inst fm0 bass`")
        events.append(FMInstrumentSelect(channel=_fm_channel(args[0], lineno),
                                         instrument=args[1]))
    elif head == "vol":
        need(2, "a channel and a level, e.g. `vol fm0 100`")
        target = _column(args[0], lineno)
        if target in _FM_COLUMNS:
            events.append(FMVolume(channel=int(target[2:]), volume=int(args[1])))
        elif target in _PSG_COLUMNS:
            events.append(PSGVolume(channel=int(target[3:]), volume=int(args[1])))
        elif target == "noise":
            events.append(PSGVolume(channel=3, volume=int(args[1])))
        else:
            raise TrackerError(f"line {lineno}: cannot set volume on {args[0]}")
    elif head == "pan":
        need(2, "a channel and L/R/C/off, e.g. `pan fm1 L`")
        side = args[1].upper()
        left = side in ("L", "C", "LR", "BOTH")
        right = side in ("R", "C", "LR", "BOTH")
        if side == "OFF":
            left = right = False
        ams = int(args[2]) if len(args) > 2 else 0
        pms = int(args[3]) if len(args) > 3 else 0
        events.append(FMPan(channel=_fm_channel(args[0], lineno), left=left,
                            right=right, ams=ams, pms=pms))
    elif head == "lfo":
        need(1, "on/off, e.g. `lfo on 4`")
        enable = args[0].lower() in ("on", "1", "true", "yes")
        freq = int(args[1]) if len(args) > 1 else 4
        events.append(FMLFO(enable=enable, freq=freq))
    elif head == "pitch":
        need(2, "a channel and cents, e.g. `pitch fm1 -12`")
        events.append(FMPitch(channel=_fm_channel(args[0], lineno),
                              cents=float(args[1])))
    elif head == "loop":
        events.append(LoopPoint())
    elif head in ("title", "author", "game", "notes"):
        setattr(meta, head, " ".join(args))
    elif head == "end":
        return True
    return False


def _column(name: str, lineno: int) -> str:
    key = name.lower()
    resolved = _COLUMN_ALIASES.get(key, key)
    if resolved not in _FM_COLUMNS + _PSG_COLUMNS + ("noise", "dac"):
        raise TrackerError(
            f"line {lineno}: unknown column {name!r}. Valid: "
            f"{', '.join(_FM_COLUMNS + _PSG_COLUMNS + ('noise', 'dac'))}")
    return resolved


def _fm_channel(name: str, lineno: int) -> int:
    column = _column(name, lineno)
    if column not in _FM_COLUMNS:
        raise TrackerError(f"line {lineno}: {name!r} is not an FM channel")
    return int(column[2:])


def _apply_cell(column, cell, events, fm_sounding, psg_sounding, lineno, raw):
    token = cell.strip()
    if token in HOLD_TOKENS:
        return
    lowered = token.lower()

    if column in _FM_COLUMNS:
        ch = int(column[2:])
        if lowered in OFF_TOKENS:
            events.append(FMNoteOff(channel=ch))
            fm_sounding[ch] = False
            return
        parsed = parse_note(token)
        if parsed is None:
            raise TrackerError(f"line {lineno}: {token!r} is not a note "
                               f"(want e.g. A-2, A#3, ===)\n  {raw.strip()}")
        note, octave, velocity = parsed
        events.append(FMNoteOn(channel=ch, note=note, octave=octave,
                               velocity=velocity if velocity else 127))
        fm_sounding[ch] = True
        return

    if column in _PSG_COLUMNS:
        ch = int(column[3:])
        if lowered in OFF_TOKENS:
            events.append(PSGToneOff(channel=ch))
            psg_sounding[ch] = False
            return
        parsed = parse_note(token)
        if parsed is None:
            raise TrackerError(f"line {lineno}: {token!r} is not a note "
                               f"(want e.g. A-4, A-4:8, ===)\n  {raw.strip()}")
        note, octave, volume = parsed
        events.append(PSGToneOn(channel=ch, note=note, octave=octave,
                                volume=volume if volume is not None else 0))
        psg_sounding[ch] = True
        return

    if column == "noise":
        if lowered in OFF_TOKENS:
            events.append(PSGNoiseOff())
            return
        m = _NOISE_CELL.match(token)
        if not m:
            raise TrackerError(f"line {lineno}: {token!r} is not a noise cell "
                               f"(want w0-w3, p0-p3, ===)\n  {raw.strip()}")
        kind, rate, volume = m.groups()
        events.append(PSGNoiseOn(white=kind.lower() == "w", rate=int(rate),
                                 volume=int(volume) if volume else 0))
        return

    if column == "dac":
        if lowered in OFF_TOKENS:
            return
        events.append(DACSample(name=token))
        return


def load(path: str):
    with open(path, encoding="utf-8") as fh:
        return loads(fh.read())


# --------------------------------------------------------------------------
# Rendering back to text
# --------------------------------------------------------------------------
def dumps(events, meta: Metadata = None, columns=None) -> str:
    """Render an event list as tracker text.

    Events are snapped to the row grid, so this is lossy for anything
    written off-grid — which is the same trade every tracker makes, and
    the reason the JSON list stays the authoritative form.
    """
    meta = meta or Metadata()
    ticks_per_row = meta.ticks_per_row()
    columns = list(columns) if columns else None

    rows = {}        # row index -> {column: cell}
    row_directives = {}   # row index -> [directive lines to print before it]
    header = []
    used = set()
    tick = 0
    max_row = 0

    def directive(row, text):
        """Row 0 directives are setup and belong in the header; later ones
        are performance and have to stay where they happened."""
        (header if row == 0 else row_directives.setdefault(row, [])).append(text)

    def row_for(t):
        return int(round(t / ticks_per_row))

    for ev in events:
        if isinstance(ev, Wait):
            tick += ev.ticks
            continue
        if isinstance(ev, End):
            break
        # max_row counts CONTENT rows, not elapsed time. A trailing Wait
        # with nothing after it is the last row's own duration, not an
        # extra empty row -- counting it would make every dump/parse cycle
        # grow the pattern by one line and never reach a fixed point.
        r = row_for(tick)
        max_row = max(max_row, r)
        cell = rows.setdefault(r, {})

        if isinstance(ev, FMInstrumentSelect):
            directive(r, f"inst fm{ev.channel} {ev.instrument}")
        elif isinstance(ev, FMPan):
            side = ("C" if ev.left and ev.right else
                    "L" if ev.left else "R" if ev.right else "off")
            extra = f" {ev.ams} {ev.pms}" if (ev.ams or ev.pms) else ""
            directive(r, f"pan fm{ev.channel} {side}{extra}")
        elif isinstance(ev, FMLFO):
            directive(r, f"lfo {'on' if ev.enable else 'off'} {ev.freq}")
        elif isinstance(ev, FMVolume):
            directive(r, f"vol fm{ev.channel} {ev.volume}")
        elif isinstance(ev, PSGVolume):
            target = "noise" if ev.channel == 3 else f"psg{ev.channel}"
            directive(r, f"vol {target} {ev.volume}")
        elif isinstance(ev, FMPitch):
            directive(r, f"pitch fm{ev.channel} {ev.cents:g}")
        elif isinstance(ev, Tempo):
            directive(r, f"ticks {ev.ticks_per_second:g}")
        elif isinstance(ev, LoopPoint):
            directive(r, "loop")
        elif isinstance(ev, FMNoteOn):
            used.add(f"fm{ev.channel}")
            suffix = f":{ev.velocity}" if ev.velocity != 127 else ""
            cell[f"fm{ev.channel}"] = _note_cell(ev.note, ev.octave) + suffix
        elif isinstance(ev, FMNoteOff):
            used.add(f"fm{ev.channel}")
            cell[f"fm{ev.channel}"] = "==="
        elif isinstance(ev, PSGToneOn):
            used.add(f"psg{ev.channel}")
            suffix = f":{ev.volume}" if ev.volume else ""
            cell[f"psg{ev.channel}"] = _note_cell(ev.note, ev.octave) + suffix
        elif isinstance(ev, PSGToneOff):
            used.add(f"psg{ev.channel}")
            cell[f"psg{ev.channel}"] = "==="
        elif isinstance(ev, PSGNoiseOn):
            used.add("noise")
            cell["noise"] = ("w" if ev.white else "p") + str(ev.rate) + \
                            (f":{ev.volume}" if ev.volume else "")
        elif isinstance(ev, PSGNoiseOff):
            used.add("noise")
            cell["noise"] = "==="
        elif isinstance(ev, DACSample):
            used.add("dac")
            cell["dac"] = ev.name

    if columns is None:
        order = _FM_COLUMNS + _PSG_COLUMNS + ("noise", "dac")
        columns = [c for c in order if c in used] or list(DEFAULT_COLUMNS)

    widths = {c: max(len(c), 3) for c in columns}
    for cells in rows.values():
        for c, text in cells.items():
            if c in widths:
                widths[c] = max(widths[c], len(text))

    out = [f"# chipgen tracker  ({meta.bpm:g} BPM, {meta.lpb} rows/beat)"]
    if meta.title:
        out.append(f"title {meta.title}")
    if meta.author:
        out.append(f"author {meta.author}")
    out.append(f"bpm {meta.bpm:g}")
    out.append(f"lpb {meta.lpb}")
    out.extend(dict.fromkeys(header))     # de-duplicated, order preserved
    out.append("cols " + " ".join(columns))
    out.append("")
    out.append("# " + " ".join(c.ljust(widths[c]) for c in columns))

    for r in range(max_row + 1):
        if r and meta.lpb and r % (meta.lpb * 4) == 0:
            out.append("")                # blank line every bar, for the eyes
        out.extend(row_directives.get(r, ()))
        cells = rows.get(r, {})
        line = " ".join(cells.get(c, "...").ljust(widths[c]) for c in columns)
        out.append(line.rstrip())
    return "\n".join(out) + "\n"


def _note_cell(note: str, octave: int) -> str:
    return f"{note[0]}{note[1] if len(note) > 1 else '-'}{octave}"


def dump(events, path: str, meta: Metadata = None, columns=None) -> str:
    text = dumps(events, meta, columns)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
