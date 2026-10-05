"""ablate.py — one mechanism taken out of a copy of a module, with the bytes
that changed said.

An A/B on someone else's module is worth its numbers only if A and B differ
in the one thing a card is about. A tracker file keeps that thing in a few
bytes — a flag in an instrument header, a node count, a command in a cell —
so B is the same file with those bytes changed, in a copy. Nothing here
opens a file: every function takes bytes and returns a `Patched` (the new
bytes and the list of what changed: offset, old, new, why). Where a change
would reach further than asked, it is refused with the reason, never made
quietly:

    it_envelope     an instrument's volume, pan or pitch/filter envelope:
                    off, its loop, sustain loop or carry off, or cut to its
                    first N nodes (it then holds the last one's value)
    it_instrument   an instrument's NNA, fade-out, or its filter off
    s3m_cell        an S3M cell's command (cleared or changed, parameter
                    kept), instrument or volume (set): S3M stores each
                    cell's fields in place, so one byte is one field of one
                    cell
    solo            every channel but some muted, by the channel's own
                    "disabled" bit (IT: pan + 128; S3M: settings + 128),
                    which both libopenmpt and Schism Tracker honour
    end_after       an end marker after an order, so a renderer that plays
                    whole songs (Schism Tracker) stops after the part read

IT pattern cells are not edited: IT packs a cell's fields with a per-channel
memory of the last ones written, so one stored command can be the command of
later cells too, and clearing it would clear them. `apply` reads changes
written as text (`inst 2 fenv off`, `cell 52 1 6 effect none`) for the
command line.
"""

import struct
from typing import Dict, Iterable, List, Optional, Tuple


class AblateError(ValueError):
    pass


#: where each envelope sits in an IT instrument header (82 bytes each)
ENVELOPE_AT = {"volume": 0x130, "pan": 0x182, "pitch": 0x1D4}
#: envelope flag bits
ENV_ON, ENV_LOOP, ENV_SUSTAIN, ENV_CARRY, ENV_FILTER = 1, 2, 4, 8, 0x80
NNA_CODES = {"cut": 0, "cont": 1, "continue": 1, "off": 2, "fade": 3}
#: S3M command letters: the byte is the letter's place in the alphabet
S3M_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class Patched:
    """A copy of a module's bytes and the record of every byte changed."""

    def __init__(self, data: bytes):
        self.original = bytes(data)
        self.data = bytearray(data)
        self.changes: List[dict] = []

    def set(self, offset: int, value: int, why: str):
        old = self.data[offset]
        if old != value:
            self.data[offset] = value
            self.changes.append({"offset": offset, "old": old, "new": value,
                                 "why": why})

    def bytes(self) -> bytes:
        return bytes(self.data)

    def record(self) -> List[dict]:
        return [dict(c) for c in self.changes]


def _patched(data, patched: Optional[Patched]) -> Patched:
    return patched if patched is not None else Patched(data)


def kind(data: bytes) -> Optional[str]:
    if data[:4] == b"IMPM":
        return "it"
    if len(data) > 0x30 and data[0x2C:0x30] == b"SCRM":
        return "s3m"
    return None


# -- IT ----------------------------------------------------------------------
def _it_counts(data: bytes) -> Tuple[int, int, int, int]:
    return struct.unpack_from("<HHHH", data, 0x20)


def it_instrument_offset(data: bytes, number: int) -> int:
    """Where instrument `number` (1-based) starts. IT's old instrument
    format (compatible-with below 2.00) keeps its envelope elsewhere and has
    no pan or pitch envelope; it is refused."""
    if kind(data) != "it":
        raise AblateError("not an IT module")
    ordnum, insnum, _, _ = _it_counts(data)
    if not 1 <= number <= insnum:
        raise AblateError(f"instrument {number}: the module has {insnum}")
    cmwt = struct.unpack_from("<H", data, 0x2A)[0]
    if cmwt < 0x200:
        raise AblateError(f"the module's instruments are in IT's old format "
                          f"(compatible with {cmwt:#x}); not handled")
    offset = struct.unpack_from("<I", data, 0xC0 + ordnum + 4 * (number - 1))[0]
    if data[offset:offset + 4] != b"IMPI":
        raise AblateError(f"instrument {number} has no header at {offset}")
    return offset


def it_envelope(data: bytes, instrument: int, slot: str, *,
                enabled: Optional[bool] = None,
                carry: Optional[bool] = None,
                loop: Optional[bool] = None,
                sustain: Optional[bool] = None,
                nodes: Optional[int] = None,
                patched: Optional[Patched] = None) -> Patched:
    """Change one envelope of an instrument. `slot` is volume, pan, pitch or
    filter (pitch and filter are one slot; asking for the one the slot is
    not is refused; a filter envelope switched off loses its filter mark
    too, see below). Only `False` is accepted for the flags: this takes
    mechanisms out, it does not add them. `nodes=N` keeps the first N nodes:
    the envelope then ends at node N-1 and holds its value."""
    p = _patched(data, patched)
    base = it_instrument_offset(data, instrument)
    at = base + ENVELOPE_AT["pitch" if slot == "filter" else slot]
    flags, count = data[at], data[at + 1]
    label = f"instrument {instrument} {slot} envelope"
    if slot in ("pitch", "filter"):
        is_filter = bool(flags & ENV_FILTER)
        if (slot == "filter") != is_filter:
            raise AblateError(f"instrument {instrument}'s pitch/filter slot "
                              f"holds a {'filter' if is_filter else 'pitch'} "
                              f"envelope, not a {slot} one")
    if not flags & ENV_ON:
        raise AblateError(f"{label} is not on")
    for name, value in (("enabled", enabled), ("carry", carry),
                        ("loop", loop), ("sustain", sustain)):
        if value not in (None, False):
            raise AblateError(f"{name}={value!r}: only False (taking it "
                              f"out) is done here")
    new = flags
    for value, bit in ((enabled, ENV_ON), (carry, ENV_CARRY), (loop, ENV_LOOP),
                       (sustain, ENV_SUSTAIN)):
        if value is False:
            if not flags & bit:
                raise AblateError(f"{label}: that flag is not set")
            new &= ~bit
    if enabled is False and slot == "filter":
        # a disabled envelope still marked "filter" is not off everywhere:
        # Schism Tracker still lowers the filter, as if the envelope stood
        # at its middle value, and libopenmpt does not (measured on Fourth
        # Symmetriad's instrument 2: a resonant peak near 820 Hz in one,
        # none in the other). With the mark cleared too, both play the
        # filter as the instrument sets it.
        new &= ~ENV_FILTER
    if nodes is not None:
        if not 1 <= nodes < count:
            raise AblateError(f"{label} has {count} nodes; keep 1-{count - 1}")
        for name, bit, first in (("loop", ENV_LOOP, at + 2),
                                 ("sustain loop", ENV_SUSTAIN, at + 4)):
            if new & bit and max(data[first], data[first + 1]) >= nodes:
                raise AblateError(f"{label}: its {name} uses node "
                                  f"{max(data[first], data[first + 1])}; take "
                                  f"the {name} out first or keep more nodes")
        p.set(at + 1, nodes, f"{label}: keep the first {nodes} of {count} "
                             f"nodes")
    if new != flags:
        p.set(at, new, f"{label}: flags {flags:#04x} -> {new:#04x}")
    return p


def it_instrument(data: bytes, instrument: int, *, nna: Optional[str] = None,
                  fadeout: Optional[int] = None, filter: Optional[bool] = None,
                  patched: Optional[Patched] = None) -> Patched:
    """An instrument's NNA (cut, cont, off, fade), fade-out (0-256), or its
    filter off (`filter=False` clears the enable bits of cutoff and
    resonance; a filter envelope then has nothing to move)."""
    p = _patched(data, patched)
    base = it_instrument_offset(data, instrument)
    if nna is not None:
        if nna not in NNA_CODES:
            raise AblateError(f"nna is one of cut, cont, off, fade")
        p.set(base + 0x11, NNA_CODES[nna],
              f"instrument {instrument}: NNA {nna}")
    if fadeout is not None:
        if not 0 <= fadeout <= 256:
            raise AblateError("fade-out is 0-256")
        p.set(base + 0x14, fadeout & 0xFF, f"instrument {instrument}: "
                                           f"fade-out {fadeout}")
        p.set(base + 0x15, fadeout >> 8, f"instrument {instrument}: "
                                         f"fade-out {fadeout}")
    if filter is not None:
        if filter is not False:
            raise AblateError("filter=False takes the filter out; nothing "
                              "else is done here")
        for off, name in ((0x3A, "cutoff"), (0x3B, "resonance")):
            p.set(base + off, data[base + off] & 0x7F,
                  f"instrument {instrument}: {name} off")
    return p


# -- S3M ---------------------------------------------------------------------
def _s3m_counts(data: bytes) -> Tuple[int, int, int]:
    return struct.unpack_from("<HHH", data, 0x20)


def s3m_cells(data: bytes) -> Dict[Tuple[int, int, int], dict]:
    """Every stored cell of an S3M: (pattern, row, channel 1-based) -> the
    offsets of its note, instrument, volume, command and info bytes (None
    for a field the cell does not store)."""
    if kind(data) != "s3m":
        raise AblateError("not an S3M module")
    ordnum, insnum, patnum = _s3m_counts(data)
    table = 0x60 + ordnum + 2 * insnum
    out = {}
    for pattern in range(patnum):
        para = struct.unpack_from("<H", data, table + 2 * pattern)[0]
        if not para:
            continue
        at = para * 16
        length = struct.unpack_from("<H", data, at)[0]
        # rows are counted to 64 whatever the length word says, so the
        # bound only keeps a damaged pattern inside the file
        pos, end, row = at + 2, min(len(data), at + 2 + length), 0
        while row < 64 and pos < end:
            what = data[pos]
            pos += 1
            if what == 0:
                row += 1
                continue
            cell = {"note": None, "instrument": None, "volume": None,
                    "command": None, "info": None}
            if what & 32:
                cell["note"], cell["instrument"] = pos, pos + 1
                pos += 2
            if what & 64:
                cell["volume"] = pos
                pos += 1
            if what & 128:
                cell["command"], cell["info"] = pos, pos + 1
                pos += 2
            out[(pattern, row, (what & 31) + 1)] = cell
    return out


def s3m_cell(data: bytes, pattern: int, row: int, channel: int, *,
             command=None, instrument: Optional[int] = None,
             volume: Optional[int] = None, cells: Optional[dict] = None,
             patched: Optional[Patched] = None) -> Patched:
    """Change one stored field of one cell (`channel` 1-based, as libopenmpt
    and the episodes number them). `command=None` leaves it; `"none"`
    clears the command and its parameter; a letter changes the command and
    keeps the parameter (K02 -> D02 keeps the slide and drops the vibrato).
    `instrument=N` replaces a stored instrument, `volume=N` (0-64) a stored
    volume. A stored volume cannot be taken away in place: the cell's mask
    says it is there, and the byte S3M's unpacked form uses for "none"
    (255) is read by libopenmpt as 64. A field the cell does not store
    cannot be added without moving the pattern. Both are refused."""
    p = _patched(data, patched)
    cells = cells if cells is not None else s3m_cells(data)
    where = f"pattern {pattern} row {row} channel {channel}"
    cell = cells.get((pattern, row, channel))
    if cell is None:
        raise AblateError(f"{where} stores nothing")
    if command is not None:
        if cell["command"] is None:
            raise AblateError(f"{where} stores no command")
        old = data[cell["command"]]
        old_text = (S3M_LETTERS[old - 1] if 1 <= old <= 26 else str(old)) \
            + f"{data[cell['info']]:02X}"
        if command == "none":
            p.set(cell["command"], 0, f"{where}: {old_text} cleared")
            p.set(cell["info"], 0, f"{where}: {old_text} cleared")
        else:
            letter = str(command).upper()
            if len(letter) != 1 or letter not in S3M_LETTERS:
                raise AblateError(f"command {command!r}: a letter A-Z or "
                                  f"'none'")
            p.set(cell["command"], S3M_LETTERS.index(letter) + 1,
                  f"{where}: {old_text} -> {letter}{data[cell['info']]:02X}")
    if instrument is not None:
        if cell["instrument"] is None:
            raise AblateError(f"{where} stores no instrument")
        if not 1 <= instrument <= 99:
            raise AblateError("an S3M instrument is 1-99")
        p.set(cell["instrument"], instrument,
              f"{where}: instrument {data[cell['instrument']]} -> "
              f"{instrument}")
    if volume is not None:
        if volume == "none":
            raise AblateError(f"{where}: a stored volume cannot be taken "
                              f"away in place (the cell's mask says it is "
                              f"there, and libopenmpt reads S3M's 'none', "
                              f"255, as 64); set it to a value 0-64 instead")
        if not 0 <= int(volume) <= 64:
            raise AblateError("an S3M volume is 0-64")
        if cell["volume"] is None:
            raise AblateError(f"{where} stores no volume")
        p.set(cell["volume"], int(volume), f"{where}: volume "
                                           f"{data[cell['volume']]} -> "
                                           f"{int(volume)}")
    return p


# -- both --------------------------------------------------------------------
def solo(data: bytes, channels: Iterable[int],
         patched: Optional[Patched] = None) -> Patched:
    """Mute every channel but `channels` (1-based)."""
    p = _patched(data, patched)
    keep = set(channels)
    fmt = kind(data)
    if fmt == "it":
        for ch in range(64):
            if ch + 1 not in keep:
                p.set(0x40 + ch, data[0x40 + ch] | 0x80,
                      f"channel {ch + 1} muted")
    elif fmt == "s3m":
        for ch in range(32):
            value = data[0x40 + ch]
            if ch + 1 not in keep and value != 0xFF:
                p.set(0x40 + ch, value | 0x80, f"channel {ch + 1} muted")
    else:
        raise AblateError("solo works on IT and S3M (their headers have a "
                          "channel-disabled bit); XM and MOD do not")
    return p


def end_after(data: bytes, order: int,
              patched: Optional[Patched] = None) -> Patched:
    """Write an end marker in the order list after `order`, so the song
    stops there. Nothing before it plays differently."""
    p = _patched(data, patched)
    fmt = kind(data)
    if fmt == "it":
        count, base = _it_counts(data)[0], 0xC0
    elif fmt == "s3m":
        count, base = _s3m_counts(data)[0], 0x60
    else:
        raise AblateError("end_after works on IT and S3M")
    if order + 1 < count:
        p.set(base + order + 1, 0xFF, f"song ends after order {order}")
    return p


# -- changes as text ---------------------------------------------------------
_SLOTS = {"venv": "volume", "penv": "pan", "ienv": "pitch", "fenv": "filter"}

GRAMMAR = """\
inst N venv|penv|ienv|fenv off            the envelope off
inst N venv|penv|ienv|fenv carry|loop|sustain off
inst N venv|penv|ienv|fenv nodes K        keep the first K nodes
inst N nna cut|cont|off|fade
inst N fade K                             fade-out 0-256
inst N filter off
cell PATTERN ROW CHANNEL effect none|LETTER   (S3M)
cell PATTERN ROW CHANNEL instrument K         (S3M)
cell PATTERN ROW CHANNEL volume 0-64          (S3M)"""


def apply(data: bytes, changes: Iterable[str],
          patched: Optional[Patched] = None) -> Patched:
    """Apply changes written as text (GRAMMAR), in order, to one copy."""
    p = _patched(data, patched)
    cells = None
    for text in changes:
        words = text.split()
        try:
            if words[0] == "inst":
                number = int(words[1])
                if words[2] in _SLOTS:
                    slot = _SLOTS[words[2]]
                    rest = words[3:]
                    if rest == ["off"]:
                        it_envelope(p.bytes(), number, slot, enabled=False,
                                    patched=p)
                    elif len(rest) == 2 and rest[1] == "off" and rest[0] in (
                            "carry", "loop", "sustain"):
                        it_envelope(p.bytes(), number, slot,
                                    **{rest[0]: False}, patched=p)
                    elif len(rest) == 2 and rest[0] == "nodes":
                        it_envelope(p.bytes(), number, slot,
                                    nodes=int(rest[1]), patched=p)
                    else:
                        raise IndexError
                elif words[2] == "nna" and len(words) == 4:
                    it_instrument(p.bytes(), number, nna=words[3], patched=p)
                elif words[2] == "fade" and len(words) == 4:
                    it_instrument(p.bytes(), number, fadeout=int(words[3]),
                                  patched=p)
                elif words[2:] == ["filter", "off"]:
                    it_instrument(p.bytes(), number, filter=False, patched=p)
                else:
                    raise IndexError
            elif words[0] == "cell" and len(words) == 6:
                if cells is None:
                    cells = s3m_cells(p.bytes())
                pattern, row, channel = (int(w) for w in words[1:4])
                field, value = words[4], words[5]
                if field == "effect":
                    s3m_cell(p.bytes(), pattern, row, channel, command=value,
                             cells=cells, patched=p)
                elif field == "instrument":
                    s3m_cell(p.bytes(), pattern, row, channel,
                             instrument=int(value), cells=cells, patched=p)
                elif field == "volume":
                    s3m_cell(p.bytes(), pattern, row, channel,
                             volume=value if value == "none" else int(value),
                             cells=cells, patched=p)
                else:
                    raise IndexError
            else:
                raise IndexError
        except (IndexError, ValueError) as error:
            if isinstance(error, AblateError):
                raise
            raise AblateError(f"{text!r} is not a change; one of:\n"
                              f"{GRAMMAR}") from None
    return p
