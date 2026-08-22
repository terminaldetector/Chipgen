"""
events.py — the event vocabulary a generative model (any architecture:
transformer, RNN, genetic/rule-based, RL agent, or a human typing them
out) outputs to drive both chips. This is the "DefleMask for neural
networks" surface: a network never has to touch FM operator physics
directly, just this flat, tokenizable event stream.

Design goals:
  - Flat sequence, self-timed via Wait events (tracker-style delta-time)
  - Every event is a plain dataclass -> trivial to_dict()/from_dict() for
    JSON, one-hot encoding, or any other model I/O convention
  - Instruments referenced by name (see instruments.BANK) rather than raw
    operator parameters, mirroring a DefleMask instrument bank
  - SPEC below is machine-readable and is the single source of truth for
    the prompt in cloud_generator.py, bridge/manifest.json and the
    validator — a field added here shows up in all three automatically.

A model's job is reduced to: "which event type, which channel, which
note/instrument, how long to wait" — a small, fixed vocabulary well
suited to autoregressive token generation.

Models make mistakes, so `parse` (as opposed to the strict
`Event.from_dict`) repairs what is unambiguously repairable — an
out-of-range channel, a lowercase note name, a missing End, a stray extra
field — and reports each repair instead of throwing the whole take away.
"""

from dataclasses import MISSING as _MISSING
from dataclasses import dataclass, asdict, fields
from typing import Any, Dict, List, Optional, Tuple

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
#: enharmonic and casing variants a model might emit, mapped to canonical names
NOTE_ALIASES = {
    "DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#",
    "E#": "F", "B#": "C", "FB": "E", "CB": "B",
}


class Event:
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.__class__.__name__
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Event":
        """Strict construction: unknown type or bad fields raise."""
        cls = _EVENT_TYPES[d["type"]]
        kwargs = {k: v for k, v in d.items() if k != "type"}
        return cls(**kwargs)

    def copy_with(self, **changes) -> "Event":
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data.update(changes)
        return type(self)(**data)


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------
@dataclass
class Wait(Event):
    """Advance the sequence clock by `ticks` ticks."""
    ticks: int


@dataclass
class Tempo(Event):
    """Change how long a tick lasts, from this point on.

    Lets one event stream carry tempo changes without the caller having to
    re-time everything: a ritardando is a few Tempo events, not a rewrite.
    """
    ticks_per_second: float


@dataclass
class LoopPoint(Event):
    """Mark where a VGM player should jump back to when the track ends.

    Ignored by the audio renderer (a WAV has no loop), consumed by vgm.py.
    """
    pass


@dataclass
class Marker(Event):
    """Free-text annotation: section names, comments, model reasoning.

    Renders to nothing. Useful for keeping a model's own structure notes
    inside the event stream instead of alongside it.
    """
    label: str = ""


# --------------------------------------------------------------------------
# YM2612 (FM) — channels 0-5
# --------------------------------------------------------------------------
@dataclass
class FMInstrumentSelect(Event):
    """Assign a preset instrument (see instruments.BANK) to an FM channel (0-5)."""
    channel: int
    instrument: str


@dataclass
class FMNoteOn(Event):
    channel: int
    note: str            # 'C','C#','D',... (see NOTE_NAMES)
    octave: int
    velocity: int = 127  # 1-127, scales carrier output level; 127 = patch as designed


@dataclass
class FMNoteOff(Event):
    channel: int


@dataclass
class FMPan(Event):
    """Stereo placement plus LFO sensitivity for one FM channel (register 0xB4).

    The chip pans per channel by hard-muting an output, not by a
    continuous law: left/right are on/off flags, and "centre" means both
    are on. ams/pms are how strongly the global LFO (see FMLFO) modulates
    this channel's amplitude and pitch.
    """
    channel: int
    left: bool = True
    right: bool = True
    ams: int = 0         # 0-3, amplitude modulation sensitivity
    pms: int = 0         # 0-7, phase (vibrato) modulation sensitivity


@dataclass
class FMLFO(Event):
    """Global LFO on/off and rate (register 0x22). Shared by all 6 channels.

    A channel only hears it if its FMPan sets ams/pms above zero — that
    split (one global rate, per-channel depth) is the chip's, not ours.
    """
    enable: bool = True
    freq: int = 4        # 0-7: ~3.98, 5.56, 6.02, 6.37, 6.88, 9.63, 48.1, 72.2 Hz


@dataclass
class FMVolume(Event):
    """Channel volume 0-127 (127 = loudest), applied to the patch's carriers.

    Attenuating a modulator would change the timbre, not the level, so this
    walks the algorithm's carrier set and adds attenuation only there.
    """
    channel: int
    volume: int = 127


@dataclass
class FMPitch(Event):
    """Detune the channel by `cents` (+/-, 100 = a semitone).

    Applies to the currently sounding note immediately and to later notes
    on the channel until changed — vibrato, pitch envelopes and slides are
    a series of these.
    """
    channel: int
    cents: float = 0.0


# --------------------------------------------------------------------------
# YM2612 DAC — channel 6 in PCM mode
# --------------------------------------------------------------------------
@dataclass
class OPLInstrumentSelect(Event):
    """Assign a patch to one of the OPL2's nine channels (0-8).

    A separate family from FMInstrumentSelect on purpose: the YM3812 is a
    different chip sitting alongside the YM2612, not a mode of it, and a
    score can drive both at once.
    """
    channel: int
    instrument: str


@dataclass
class OPLNoteOn(Event):
    channel: int
    note: str
    octave: int
    velocity: int = 127


@dataclass
class OPLNoteOff(Event):
    channel: int


@dataclass
class OPLVolume(Event):
    """Channel volume, 0-127, on the same linear-in-amplitude scale as FM."""
    channel: int
    volume: int = 127


@dataclass
class OPLDepth(Event):
    """The chip's two global LFO depths (register 0xBD).

    They are global, not per channel — one tremolo depth and one vibrato
    depth for all nine voices — and only operators with their AM or VIB
    bit set are affected at all.
    """
    tremolo: int = 0     # 0 = 1.0 dB, 1 = 4.8 dB
    vibrato: int = 0     # 0 = 7 cents, 1 = 14 cents


@dataclass
class Portamento(Event):
    """Slide a voice's pitch toward `to_cents` at `cents_per_second`.

    `target` names the voice the way a tracker column does — "fm0",
    "psg1", "opl3" — because chipgen drives three chips now and an int
    channel cannot say which one it means.

    The slide stops on arrival and stays there, so `to_cents=0` is how a
    bend returns to the written pitch. A rate of 0 stops it where it is.
    """
    target: str
    cents_per_second: float = 0.0
    to_cents: float = 0.0


@dataclass
class Vibrato(Event):
    """Swing a voice's pitch by +/- depth_cents at speed_hz.

    `delay` holds it off for that many seconds after each note-on, which
    is what makes a vibrato sound played rather than switched on.
    Depth or speed of 0 turns it off.
    """
    target: str
    depth_cents: float = 0.0
    speed_hz: float = 0.0
    delay: float = 0.0


@dataclass
class VolumeSlide(Event):
    """Ramp a voice's level by `per_second`, in the same 0-127 units as
    FMVolume, bounded by floor and ceiling. Negative fades out."""
    target: str
    per_second: float = 0.0
    floor: int = 0
    ceiling: int = 127


@dataclass
class Tremolo(Event):
    """Swing a voice's level by +/- depth (0-127 units) at speed_hz."""
    target: str
    depth: float = 0.0
    speed_hz: float = 0.0


@dataclass
class DACEnable(Event):
    """Switch FM channel 6 between normal FM and the 8-bit PCM DAC (register 0x2B)."""
    enable: bool = True


@dataclass
class DACSample(Event):
    """Play a PCM sample through the DAC. `name` indexes samples.KIT.

    This is the Genesis drum trick: FM channel 6 gives up its voice and
    becomes an 8-bit sample player, which is how Streets of Rage and
    Contra: Hard Corps get real drums out of the chip.
    """
    name: str
    rate: int = 0        # playback rate in Hz; 0 = the sample's own rate
    volume: float = 1.0  # 0.0-1.0 scaling before the 8-bit quantisation


# --------------------------------------------------------------------------
# SN76489 (PSG) — tone channels 0-2 + shared noise
# --------------------------------------------------------------------------
@dataclass
class PSGToneOn(Event):
    channel: int         # 0-2
    note: str
    octave: int
    volume: int = 0      # 0 = loudest, 15 = silent


@dataclass
class PSGToneOff(Event):
    channel: int


@dataclass
class PSGVolume(Event):
    """Set a PSG channel's attenuation without retriggering its note.

    Channel 3 is the noise voice. PSG has no envelope generator at all, so
    a volume ramp — every PSG decay you have ever heard — is literally a
    run of these events.
    """
    channel: int         # 0-2 tone, 3 = noise
    volume: int = 0      # 0 = loudest, 15 = silent


@dataclass
class PSGNoiseOn(Event):
    """Gate the shared noise voice on.

    `restart` forces a write to the noise register, which resets the LFSR.
    Leave it off for hats and cymbals: re-seeding the shift register on
    every hit makes the noise repeat itself and turns a cymbal into a
    buzz. Turn it on when you want a short blip to sound identical each
    time, which is the one case the reset is good for.
    """
    white: bool          # True = white noise, False = "periodic" noise
    rate: int            # 0,1,2 = fixed rates; 3 = synced to PSG tone channel 2
    volume: int = 0
    restart: bool = False


@dataclass
class PSGNoiseOff(Event):
    pass


@dataclass
class End(Event):
    pass


_EVENT_TYPES = {
    "Wait": Wait,
    "Tempo": Tempo,
    "LoopPoint": LoopPoint,
    "Marker": Marker,
    "FMInstrumentSelect": FMInstrumentSelect,
    "FMNoteOn": FMNoteOn,
    "FMNoteOff": FMNoteOff,
    "FMPan": FMPan,
    "FMLFO": FMLFO,
    "FMVolume": FMVolume,
    "FMPitch": FMPitch,
    "Portamento": Portamento,
    "Vibrato": Vibrato,
    "VolumeSlide": VolumeSlide,
    "Tremolo": Tremolo,
    "OPLInstrumentSelect": OPLInstrumentSelect,
    "OPLNoteOn": OPLNoteOn,
    "OPLNoteOff": OPLNoteOff,
    "OPLVolume": OPLVolume,
    "OPLDepth": OPLDepth,
    "DACEnable": DACEnable,
    "DACSample": DACSample,
    "PSGToneOn": PSGToneOn,
    "PSGToneOff": PSGToneOff,
    "PSGVolume": PSGVolume,
    "PSGNoiseOn": PSGNoiseOn,
    "PSGNoiseOff": PSGNoiseOff,
    "End": End,
}

#: Numeric fields and their inclusive bounds, used by the repairing parser
#: and published to models via bridge/manifest.json.
SPEC: Dict[str, Dict[str, Any]] = {
    "Wait":               {"ticks": (0, 1_000_000)},
    "Tempo":              {"ticks_per_second": (1, 100_000)},
    "LoopPoint":          {},
    "Marker":             {},
    "FMInstrumentSelect": {"channel": (0, 5)},
    "FMNoteOn":           {"channel": (0, 5), "octave": (0, 9), "velocity": (1, 127)},
    "FMNoteOff":          {"channel": (0, 5)},
    "FMPan":              {"channel": (0, 5), "ams": (0, 3), "pms": (0, 7)},
    "FMLFO":              {"freq": (0, 7)},
    "FMVolume":           {"channel": (0, 5), "volume": (0, 127)},
    "FMPitch":            {"channel": (0, 5), "cents": (-4800, 4800)},
    "Portamento":         {"cents_per_second": (-48000, 48000),
                           "to_cents": (-4800, 4800)},
    "Vibrato":            {"depth_cents": (0, 2400), "speed_hz": (0, 40),
                           "delay": (0.0, 10.0)},
    "VolumeSlide":        {"per_second": (-1000, 1000), "floor": (0, 127),
                           "ceiling": (0, 127)},
    "Tremolo":            {"depth": (0, 127), "speed_hz": (0, 40)},
    "OPLInstrumentSelect": {"channel": (0, 8)},
    "OPLNoteOn":          {"channel": (0, 8), "octave": (0, 9), "velocity": (1, 127)},
    "OPLNoteOff":         {"channel": (0, 8)},
    "OPLVolume":          {"channel": (0, 8), "volume": (0, 127)},
    "OPLDepth":           {"tremolo": (0, 1), "vibrato": (0, 1)},
    "DACEnable":          {},
    "DACSample":          {"rate": (0, 96_000), "volume": (0.0, 1.0)},
    "PSGToneOn":          {"channel": (0, 2), "octave": (0, 9), "volume": (0, 15)},
    "PSGToneOff":         {"channel": (0, 2)},
    "PSGVolume":          {"channel": (0, 3), "volume": (0, 15)},
    "PSGNoiseOn":         {"rate": (0, 3), "volume": (0, 15)},
    "PSGNoiseOff":        {},
    "End":                {},
}

#: type-name spellings a model plausibly emits -> canonical name
TYPE_ALIASES = {
    "noteon": "FMNoteOn", "noteoff": "FMNoteOff", "fmnote": "FMNoteOn",
    "instrument": "FMInstrumentSelect", "fminstrument": "FMInstrumentSelect",
    "instrumentselect": "FMInstrumentSelect", "setinstrument": "FMInstrumentSelect",
    "delay": "Wait", "rest": "Wait", "sleep": "Wait",
    "psgnote": "PSGToneOn", "psgon": "PSGToneOn", "psgoff": "PSGToneOff",
    "noiseon": "PSGNoiseOn", "noiseoff": "PSGNoiseOff",
    "pan": "FMPan", "lfo": "FMLFO", "volume": "FMVolume", "pitch": "FMPitch",
    "dac": "DACSample", "sample": "DACSample",
    "porta": "Portamento", "slide": "Portamento", "bend": "Portamento",
    "vib": "Vibrato", "volslide": "VolumeSlide", "fade": "VolumeSlide",
    "trem": "Tremolo",
    "oplnote": "OPLNoteOn", "oplon": "OPLNoteOn", "oploff": "OPLNoteOff",
    "oplinstrument": "OPLInstrumentSelect", "adlib": "OPLNoteOn",
    "loop": "LoopPoint", "comment": "Marker", "stop": "End", "finish": "End",
}


def event_types() -> List[str]:
    return list(_EVENT_TYPES)


def field_names(type_name: str) -> List[str]:
    return [f.name for f in fields(_EVENT_TYPES[type_name])]


def describe_vocabulary() -> Dict[str, Any]:
    """Machine-readable dump of the whole vocabulary: types, fields,
    defaults and numeric ranges. Consumed by bridge/manifest.json and by
    the cloud prompt so the three can never drift apart."""
    out = {}
    for name, cls in _EVENT_TYPES.items():
        params = {}
        for f in fields(cls):
            entry: Dict[str, Any] = {"type": _type_name(f.type)}
            if f.default is not _MISSING:
                entry["default"] = f.default
            rng = SPEC.get(name, {}).get(f.name)
            if rng:
                entry["range"] = list(rng)
            if f.name == "note":
                entry["values"] = list(NOTE_NAMES)
            params[f.name] = entry
        out[name] = {"fields": params, "doc": _real_doc(cls)}
    return out


def _real_doc(cls) -> str:
    """Dataclasses synthesise a signature-shaped __doc__; that is noise here."""
    doc = (cls.__doc__ or "").strip()
    return "" if doc.startswith(cls.__name__ + "(") else doc


def _type_name(t) -> str:
    return getattr(t, "__name__", str(t))


# --------------------------------------------------------------------------
# Tolerant parsing
# --------------------------------------------------------------------------
def normalize_note(note: Any) -> Optional[str]:
    """'db5' -> 'C#', 'e' -> 'E', 60 -> 'C'. None if unrecognisable."""
    if isinstance(note, int):
        return NOTE_NAMES[note % 12]
    if not isinstance(note, str):
        return None
    text = note.strip().replace("♯", "#").replace("♭", "b")
    # tolerate a trailing octave digit glued to the name ("A#4")
    while text and text[-1].isdigit():
        text = text[:-1]
    if not text:
        return None
    upper = text.upper()
    if upper in NOTE_ALIASES:
        return NOTE_ALIASES[upper]
    canonical = upper[0] + upper[1:].replace("B", "b")
    if canonical in NOTE_NAMES:
        return canonical
    if upper in NOTE_NAMES:
        return upper
    return None


def _coerce(value, target, warn, where: str):
    if target is bool:
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
    if target is int:
        try:
            return int(round(float(value)))
        except (TypeError, ValueError):
            warn(f"{where}: {value!r} is not a number, using 0")
            return 0
    if target is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            warn(f"{where}: {value!r} is not a number, using 0")
            return 0.0
    return value if isinstance(value, str) else str(value)


def parse_event(d: Dict[str, Any], warn=None, index: int = -1) -> Optional[Event]:
    """Build one Event from a loose dict, repairing what is repairable.

    Returns None (with a warning) only when the event cannot be salvaged
    at all — an unknown type, or a missing field with no sane default.
    """
    warnings: List[str] = []
    warn = warn if warn is not None else warnings.append
    where = f"event #{index}" if index >= 0 else "event"

    if not isinstance(d, dict):
        warn(f"{where}: not an object ({type(d).__name__}), dropped")
        return None

    raw_type = d.get("type") or d.get("event") or d.get("kind")
    if raw_type is None:
        warn(f"{where}: no 'type' field, dropped")
        return None
    type_name = _resolve_type(str(raw_type))
    if type_name is None:
        warn(f"{where}: unknown type {raw_type!r}, dropped")
        return None
    if type_name != str(raw_type):
        warn(f"{where}: type {raw_type!r} read as {type_name}")

    cls = _EVENT_TYPES[type_name]
    known = {f.name: f for f in fields(cls)}
    kwargs: Dict[str, Any] = {}

    for key, value in d.items():
        if key in ("type", "event", "kind"):
            continue
        field = known.get(key) or known.get(_ALIAS_FIELDS.get(key, ""))
        if field is None:
            warn(f"{where} ({type_name}): ignoring unknown field {key!r}")
            continue
        if field.name == "note":
            note = normalize_note(value)
            if note is None:
                warn(f"{where}: note {value!r} not recognised, using C")
                note = "C"
            elif note != value:
                warn(f"{where}: note {value!r} read as {note}")
            kwargs["note"] = note
            continue
        kwargs[field.name] = _coerce(value, field.type, warn,
                                     f"{where} ({type_name}.{field.name})")

    for name, field in known.items():
        if name in kwargs:
            continue
        if field.default is _MISSING:
            warn(f"{where} ({type_name}): missing required field {name!r}, using default")
            kwargs[name] = _default_for(field.type)

    event = cls(**kwargs)
    return _clamp(event, type_name, warn, where)


def _resolve_type(raw: str) -> Optional[str]:
    if raw in _EVENT_TYPES:
        return raw
    squashed = raw.replace("_", "").replace("-", "").replace(" ", "").lower()
    for name in _EVENT_TYPES:
        if name.lower() == squashed:
            return name
    return TYPE_ALIASES.get(squashed)


_ALIAS_FIELDS = {"ch": "channel", "chan": "channel", "instr": "instrument",
                 "inst": "instrument", "oct": "octave", "vol": "volume",
                 "dur": "ticks", "duration": "ticks", "len": "ticks",
                 "length": "ticks", "wait": "ticks", "vel": "velocity",
                 "text": "label", "name": "label", "value": "cents"}


def _default_for(target):
    if target is bool:
        return False
    if target is int:
        return 0
    if target is float:
        return 0.0
    return ""


def _clamp(event: Event, type_name: str, warn, where: str) -> Event:
    changes = {}
    for field_name, (lo, hi) in SPEC.get(type_name, {}).items():
        value = getattr(event, field_name, None)
        if value is None:
            continue
        if value < lo or value > hi:
            fixed = max(lo, min(hi, value))
            warn(f"{where} ({type_name}.{field_name}): {value} out of range "
                 f"[{lo}, {hi}], clamped to {fixed}")
            changes[field_name] = fixed
    return event.copy_with(**changes) if changes else event


def parse(data: List[Dict[str, Any]]) -> Tuple[List[Event], List[str]]:
    """Tolerant bulk parse. Returns (events, warnings).

    Always returns a sequence terminated by End, because a model that
    forgets the terminator has still told you everything else it meant.
    """
    warnings: List[str] = []
    events: List[Event] = []
    for i, item in enumerate(data):
        event = parse_event(item, warnings.append, i)
        if event is not None:
            events.append(event)
    if not events or not isinstance(events[-1], End):
        warnings.append("sequence did not end with End, appended one")
        events.append(End())
    return events, warnings


# --------------------------------------------------------------------------
# JSON helpers (unchanged public API)
# --------------------------------------------------------------------------
def events_to_json(events: List[Event]) -> list:
    return [e.to_dict() for e in events]


def events_from_json(data: list) -> List[Event]:
    """Strict counterpart to parse(): raises on anything malformed."""
    return [Event.from_dict(d) for d in data]


def total_ticks(events: List[Event]) -> int:
    return sum(e.ticks for e in events if isinstance(e, Wait))


def duration_seconds(events: List[Event], ticks_per_second: float = 192.0) -> float:
    """Account for Tempo changes, so this matches what the sequencer renders."""
    rate = ticks_per_second
    seconds = 0.0
    for e in events:
        if isinstance(e, Tempo):
            rate = max(1.0, float(e.ticks_per_second))
        elif isinstance(e, Wait):
            seconds += e.ticks / rate
        elif isinstance(e, End):
            break
    return seconds
