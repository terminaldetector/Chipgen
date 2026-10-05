"""model.py — an Impulse Tracker module, as plain data.

Everything the `.it` format can say, and nothing else: no notation, no
synthesis, no file layout. `it_write` turns a Module into bytes, `it_read`
turns bytes into a Module, `notation` turns text into one. Keeping the
model in the middle is what lets all three be checked against each other
and against libopenmpt.

Numbers follow the format, not convention, and every limit below is the
format's own:

    notes         1..120 are C-0..B-9 (so C-5 is 61, A-4 is 58 — one more
                  than the byte IT stores, which counts from zero);
                  NOTE_OFF, NOTE_CUT and NOTE_FADE are the three specials
    instruments   1..99 in Impulse Tracker; the file allows more
    samples       1..99 likewise
    volume column 0..64 volume, 65..212 the slide/porta/vibrato commands,
                  128..192 pan — see `volume_column`
    effects       1..26, A..Z; the parameter is one byte
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# -- notes ------------------------------------------------------------------
NOTE_OFF = 255          # ===  key off
NOTE_CUT = 254          # ^^^  cut
NOTE_FADE = 253         # ~~~  fade
NOTE_NAMES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-",
              "A#", "B-")
MIN_NOTE, MAX_NOTE = 1, 120

MAX_CHANNELS = 64
MIN_ROWS, MAX_ROWS = 32, 200

#: Impulse Tracker's own ceilings. The file format has room for more, and
#: Schism will read more, but a module that stays inside these opens in
#: every tracker that ever read .it.
IT_MAX_INSTRUMENTS = 99
#: Impulse Tracker 2.14 itself loads 99 samples and 200 patterns. Schism
#: Tracker (measured, 2024-01-29 build) plays samples 1..235 — the 236th is
#: silent and a file with 237 never loads — and patterns up to 240, and
#: OpenMPT stops at 240 patterns too. The writer refuses more than that
#: rather than write a file that opens in some players and hangs in others.
IT_MAX_SAMPLES = 99
SCHISM_MAX_SAMPLES = 235
IT_MAX_PATTERNS = 200
SCHISM_MAX_PATTERNS = 240


def note_name(note: int) -> str:
    """1 -> 'C-0', 61 -> 'C-5', 253..255 -> '~~~', '^^^', '==='."""
    if note == 0:
        return "..."
    if note == NOTE_OFF:
        return "==="
    if note == NOTE_CUT:
        return "^^^"
    if note == NOTE_FADE:
        return "~~~"
    index = note - 1
    return f"{NOTE_NAMES[index % 12]}{index // 12}"


def note_number(name: str) -> int:
    """'C-5' or 'C#5' -> 61; '===' '^^^' '~~~' '...' -> the specials / 0."""
    name = name.strip()
    specials = {"...": 0, "===": NOTE_OFF, "^^^": NOTE_CUT, "~~~": NOTE_FADE}
    if name in specials:
        return specials[name]
    if len(name) != 3 or name[:2] not in NOTE_NAMES or not name[2].isdigit():
        raise ValueError(f"{name!r} is not a note (want C-5, C#5, ===, ^^^, "
                         f"~~~ or ...)")
    return NOTE_NAMES.index(name[:2]) + 12 * int(name[2]) + 1


def note_frequency(note: int) -> float:
    """Hz at the module's own tuning, where A-4 (note 58) is 440 Hz and
    C-5 (note 61) is 523.2511 Hz."""
    return 440.0 * 2.0 ** ((note - 58) / 12.0)


# -- volume column ----------------------------------------------------------
# value ranges in the file
_VC_FINE_UP, _VC_FINE_DOWN = 65, 75
_VC_SLIDE_UP, _VC_SLIDE_DOWN = 85, 95
_VC_PITCH_DOWN, _VC_PITCH_UP = 105, 115
_VC_PAN = 128
_VC_PORTA, _VC_VIBRATO = 193, 203

#: letter -> first file value of its ten-value range (a..h), as every
#: tracker's volume column spells them.
VOLUME_LETTERS = {"a": _VC_FINE_UP, "b": _VC_FINE_DOWN, "c": _VC_SLIDE_UP,
                  "d": _VC_SLIDE_DOWN, "e": _VC_PITCH_DOWN,
                  "f": _VC_PITCH_UP, "g": _VC_PORTA, "h": _VC_VIBRATO}

#: What each letter does, for messages and docs.
VOLUME_MEANING = {
    "v": "set volume 0-64", "p": "set panning 0 (left) - 64 (right)",
    "a": "fine volume up 0-9", "b": "fine volume down 0-9",
    "c": "volume slide up 0-9", "d": "volume slide down 0-9",
    "e": "pitch slide down 0-9", "f": "pitch slide up 0-9",
    "g": "tone portamento 0-9", "h": "vibrato depth 0-9"}


def volume_column(text: str) -> int:
    """'v48' -> 48, 'p32' -> 160, 'c05' -> 90 ... '...' -> -1."""
    text = text.strip().lower()
    if text in ("...", "", "-1"):
        return -1
    if len(text) != 3 or not text[1:].isdigit() or text[0] not in "vp" + "".join(VOLUME_LETTERS):
        raise ValueError(f"{text!r} is not a volume-column entry (want v00-v64, "
                         f"p00-p64, or a-h with 00-09, or ...)")
    amount = int(text[1:])
    if text[0] == "v":
        if amount > 64:
            raise ValueError(f"{text!r}: volume is 0-64")
        return amount
    if text[0] == "p":
        if amount > 64:
            raise ValueError(f"{text!r}: panning is 0 (left) to 64 (right)")
        return _VC_PAN + amount
    if amount > 9:
        raise ValueError(f"{text!r}: {VOLUME_MEANING[text[0]]}")
    return VOLUME_LETTERS[text[0]] + amount


def volume_column_text(value: int) -> str:
    if value < 0:
        return "..."
    if value <= 64:
        return f"v{value:02d}"
    if _VC_PAN <= value <= _VC_PAN + 64:
        return f"p{value - _VC_PAN:02d}"
    for letter, first in VOLUME_LETTERS.items():
        if first <= value < first + 10:
            return f"{letter}{value - first:02d}"
    raise ValueError(f"{value} is not a volume-column value")


# -- effects ----------------------------------------------------------------
EFFECT_LETTERS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: A..Z with what each one is, from ITTECH. S is a family: the first
#: nibble of its parameter picks the effect.
EFFECT_MEANING = {
    "A": "set speed (ticks per row)", "B": "jump to order",
    "C": "break to row of the next pattern", "D": "volume slide",
    "E": "pitch slide down", "F": "pitch slide up",
    "G": "tone portamento", "H": "vibrato", "I": "tremor",
    "J": "arpeggio", "K": "vibrato + volume slide",
    "L": "tone portamento + volume slide", "M": "set channel volume",
    "N": "channel volume slide", "O": "sample offset", "P": "panning slide",
    "Q": "retrigger", "R": "tremolo", "S": "special (Sxy)",
    "T": "tempo", "U": "fine vibrato", "V": "global volume",
    "W": "global volume slide", "X": "set panning 00-FF",
    "Y": "panbrello", "Z": "MIDI macro / filter"}


def effect_text(effect: int, param: int) -> str:
    if effect == 0 and param == 0:
        return "..."
    return f"{EFFECT_LETTERS[effect]}{param:02X}"


def effect_parse(text: str):
    """'H44' -> (8, 0x44); '...' -> (0, 0)."""
    text = text.strip().upper()
    if text in ("...", ""):
        return 0, 0
    if len(text) != 3 or text[0] not in EFFECT_LETTERS[1:]:
        raise ValueError(f"{text!r} is not an effect (want a letter A-Z and "
                         f"two hex digits, like H44, or ...)")
    try:
        param = int(text[1:], 16)
    except ValueError:
        raise ValueError(f"{text!r}: the parameter is two hex digits") from None
    return EFFECT_LETTERS.index(text[0]), param


# -- the module -------------------------------------------------------------
@dataclass
class Cell:
    note: int = 0            # 0 none, 1..120, or NOTE_OFF / NOTE_CUT / NOTE_FADE
    instrument: int = 0      # 0 none
    volume: int = -1         # -1 none, else the volume-column value
    effect: int = 0          # 0 none, 1..26
    param: int = 0

    def empty(self) -> bool:
        return (not self.note and not self.instrument and self.volume < 0
                and not self.effect and not self.param)

    def text(self) -> str:
        """The cell as every tracker shows it: `C-5 01 v64 H44`."""
        ins = f"{self.instrument:02d}" if self.instrument else ".."
        return (f"{note_name(self.note)} {ins} "
                f"{volume_column_text(self.volume)} "
                f"{effect_text(self.effect, self.param)}")


@dataclass
class Pattern:
    rows: int = 64
    cells: List[List[Cell]] = field(default_factory=list)   # [row][channel]
    name: str = ""

    def __post_init__(self):
        if not self.cells:
            self.cells = [[] for _ in range(self.rows)]

    def cell(self, row: int, channel: int) -> Cell:
        """The cell at (row, channel), growing the row if need be."""
        line = self.cells[row]
        while len(line) <= channel:
            line.append(Cell())
        return line[channel]


@dataclass
class Envelope:
    """Up to 25 (tick, value) nodes.

    Values: volume 0..64; panning -32..+32; pitch -32..+32 (half
    semitones); filter 0..64 cutoff. `loop` and `sustain` are (first, last)
    NODE indices, not ticks.
    """
    nodes: List[Tuple[int, int]] = field(default_factory=list)
    loop: Optional[Tuple[int, int]] = None
    sustain: Optional[Tuple[int, int]] = None
    enabled: bool = True
    filter: bool = False        # pitch slot only: the envelope moves the filter
    #: a new note picks the envelope up where the last one left it instead
    #: of starting it again (IT's "carry", flag bit 3)
    carry: bool = False


@dataclass
class Instrument:
    name: str = ""
    filename: str = ""
    nna: int = 0                # 0 cut, 1 continue, 2 note off, 3 note fade
    dct: int = 0                # duplicate check: 0 off, 1 note, 2 sample, 3 instrument
    dca: int = 0                # duplicate action: 0 cut, 1 note off, 2 note fade
    fadeout: int = 0            # 0..256
    pitch_pan_separation: int = 0      # -32..32
    pitch_pan_center: int = 60         # note 0..119
    global_volume: int = 128    # 0..128
    default_pan: Optional[int] = None  # 0..64, None = the channel's own
    random_volume: int = 0      # percent
    random_pan: int = 0
    cutoff: Optional[int] = None       # 0..127, None = filter off
    resonance: Optional[int] = None    # 0..127
    midi_channel: int = 0
    midi_program: int = 0
    midi_bank: int = 0
    #: 120 entries, one per note C-0..B-9: (note to play, sample 1-based; 0 = none)
    note_map: List[Tuple[int, int]] = field(default_factory=list)
    volume_envelope: Optional[Envelope] = None
    pan_envelope: Optional[Envelope] = None
    pitch_envelope: Optional[Envelope] = None

    def __post_init__(self):
        if not self.note_map:
            self.note_map = [(n, 0) for n in range(120)]

    def use_sample(self, sample: int, lowest: int = 1, highest: int = 120):
        """Map notes `lowest`..`highest` (1-based, as `Cell.note`) to
        `sample`, played at their own pitch."""
        for note in range(lowest, highest + 1):
            self.note_map[note - 1] = (note - 1, sample)


@dataclass
class Sample:
    name: str = ""
    filename: str = ""
    data: object = None         # array('h') for 16-bit, array('b') for 8-bit
    #: the right channel of a stereo sample, as long as `data`, which is then
    #: the left; None for a mono sample. The file stores all of the left and
    #: then all of the right (measured: libopenmpt and Schism Tracker read
    #: it that way, and read interleaved frames as noise)
    right: object = None
    bits: int = 16
    c5speed: int = 44100        # Hz at which note C-5 plays the data unshifted
    volume: int = 64            # default volume 0..64
    global_volume: int = 64
    default_pan: Optional[int] = None   # 0..64
    loop: Optional[Tuple[int, int]] = None      # (begin, end) frames, end exclusive
    loop_pingpong: bool = False
    sustain_loop: Optional[Tuple[int, int]] = None
    sustain_pingpong: bool = False
    vibrato_speed: int = 0      # 0..64
    vibrato_depth: int = 0      # 0..64
    vibrato_rate: int = 0       # 0..64, sweep
    vibrato_wave: int = 0       # 0 sine, 1 ramp down, 2 square, 3 random
    #: set by the reader when the file stored the data compressed and the
    #: reader did not unpack it (the header is still real)
    compressed: bool = False
    #: the frames the file holds when the reader left them there (a
    #: compressed sample, or a read with load_data=False): `data` is None but
    #: the sample is not empty, and writing it would drop the audio
    stored_frames: int = 0

    @property
    def frames(self) -> int:
        return 0 if self.data is None else len(self.data)

    @property
    def channels(self) -> int:
        return 1 if self.right is None else 2


@dataclass
class Module:
    title: str = ""
    rows_per_beat: int = 4          # the highlight the editor draws
    rows_per_measure: int = 16
    stereo: bool = True
    use_instruments: bool = True
    linear_slides: bool = True
    old_effects: bool = False
    gxx_link: bool = False          # Gxx shares memory with Exx/Fxx
    global_volume: int = 128        # 0..128
    mix_volume: int = 48            # 0..128
    speed: int = 6
    tempo: int = 125
    pan_separation: int = 128       # 0..128
    pitch_wheel_depth: int = 0
    #: per channel: 0..64 panning, 100 = surround; muted channels are
    #: flagged separately so the pan survives
    channel_pan: List[int] = field(default_factory=lambda: [32] * 64)
    channel_volume: List[int] = field(default_factory=lambda: [64] * 64)
    channel_mute: List[bool] = field(default_factory=lambda: [False] * 64)
    orders: List[int] = field(default_factory=list)   # 254 skip, 255 end are added on write
    patterns: List[Pattern] = field(default_factory=list)
    instruments: List[Instrument] = field(default_factory=list)
    samples: List[Sample] = field(default_factory=list)
    message: str = ""
    midi_config: Optional[bytes] = None   # 4896 bytes, or None for IT's defaults
    #: tracker that wrote it: (Cwt/v, Cmwt), for the reader to report
    created_with: int = 0x0214
    compatible_with: int = 0x0214

    def channels_used(self) -> int:
        """Highest channel with anything in it, plus one."""
        most = 0
        for pattern in self.patterns:
            for row in pattern.cells:
                for channel, cell in enumerate(row):
                    if not cell.empty():
                        most = max(most, channel + 1)
        return most

    def seconds(self) -> float:
        """Playing time, following the order list the way a player does.

        Walks the effects that change time — A speed, T tempo, B jump, C
        break, SEx pattern delay (x extra rows), S6x fine delay (x ticks) —
        and wraps past the last order to the first, as Impulse Tracker
        does; the walk ends when it would play a row it has already played.
        Row loops (SBx) are not followed. The authority on duration is
        libopenmpt (`openmpt.Song.duration`), and the tests hold the two
        together across every effect listed here.
        """
        speed, tempo = self.speed, self.tempo
        total = 0.0
        order, row = 0, 0
        visited = set()
        guard = 0
        while self.orders and guard < 200000:
            guard += 1
            if order >= len(self.orders):
                order = 0
            index = self.orders[order]
            if index == 255:
                break
            if index == 254 or index >= len(self.patterns):
                order, row = order + 1, 0
                continue
            pattern = self.patterns[index]
            if row >= pattern.rows:
                order, row = order + 1, 0
                continue
            if (order, row) in visited:
                break
            visited.add((order, row))
            jump = brk = None
            repeat = 0
            fine = 0
            for cell in pattern.cells[row]:
                letter = EFFECT_LETTERS[cell.effect] if cell.effect else ""
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
                elif letter == "S" and cell.param >> 4 == 0x6:
                    fine += cell.param & 0xF
            total += (speed * (1 + repeat) + fine) * 2.5 / tempo
            if jump is not None:
                order, row = jump, brk or 0
            elif brk is not None:
                order, row = order + 1, brk
            else:
                row += 1
                if row >= pattern.rows:
                    order, row = order + 1, 0
        return total
