"""probe.py — play one note through the real player and read it back.

A probe is a one-note module: the instrument, a note at the register, a
key-off after the hold, and silence for the tail — rendered by libopenmpt,
the same engine that plays the finished song, and run through
`descriptors.measure`. It is the only place the search touches audio, and
it is cached: two genomes that compile to the same instrument cost one
render.

`full=True` holds the note 2.4 s (detune needs a second of steady note
after the attack and the fall); the default quick probe holds it 0.84 s and
skips the detune and motion readings. The search runs quick probes and gives
finalists a full one.

An instrument with unison or vibrato is rendered twice on a full probe: the
plain voice for every dial but detune and motion (two voices beating shift
the level at whatever moment it is read, so attack, body and decay would
follow the beat), and the whole instrument for those two.
"""

import json
import time
from dataclasses import dataclass
from typing import Optional

from .. import it_write, model as M, openmpt
from . import descriptors, dsp, patch as patch_mod, validate
from .family import Compiled, get_family
from .patch import REFERENCE_TICK

SAMPLE_RATE = 44100
SPEED = 6
#: seconds of the loudest stretch that counts as an instrument's level
LEVEL_WINDOW = 0.12
_CACHE = {}
_CACHE_LIMIT = 4096


class ProbeUnavailable(RuntimeError):
    """libopenmpt is not installed: nothing can be measured."""


def available() -> bool:
    return openmpt.available()


def note_of(name: str, octave: int) -> int:
    """('A', 3) -> IT note number 46 (A-3, 220 Hz). Sharps: ('C#', 4)."""
    name = name.strip().upper()
    label = name + ("-" if len(name) == 1 else "") + str(octave)
    return M.note_number(label)


def note_hz(note: int) -> float:
    return M.note_frequency(note)


@dataclass
class ProbeResult:
    measurement: descriptors.Measurement
    issues: list
    level: float = 0.0               # loudest 120 ms, RMS
    seconds: float = 0.0             # what the probe cost
    cached: bool = False
    note: str = ""

    @property
    def axes(self):
        return self.measurement.axes

    @property
    def ok(self) -> bool:
        return not validate.errors(self.issues)


def probe_module(patch, note: int, hold_rows: int, rows: int,
                 tick: float = REFERENCE_TICK, velocity: int = 127,
                 key: Optional[int] = None) -> M.Module:
    """The one-note module. The instrument is number 1 on channel 1, and
    only the samples that serve `note` are built."""
    tempo = int(round(2.5 / tick))
    if not 32 <= tempo <= 255:
        raise ValueError(f"a tick of {tick * 1000:.1f} ms is not a tempo the "
                         f"format holds (32..255)")
    module = M.Module(title="probe", speed=SPEED, tempo=tempo, mix_volume=48,
                      global_volume=128)
    patch_mod.install(patch, module, 1, tick, "probe", want=(note, note))
    pattern = M.Pattern(rows=max(M.MIN_ROWS, rows))
    cell = pattern.cell(0, 0)
    cell.note = note if key is None else key
    cell.instrument = 1
    cell.volume = max(1, min(64, int(round(velocity / 127.0 * 64))))
    pattern.cell(hold_rows, 0).note = M.NOTE_OFF
    module.patterns.append(pattern)
    module.orders = [0]
    return module


def render_module(module: M.Module, seconds: float, rate: int = SAMPLE_RATE):
    """-> (left, right) as lists."""
    if not openmpt.available():
        raise ProbeUnavailable("libopenmpt is not installed "
                               "(apt install libopenmpt0)")
    with openmpt.Song(it_write.build(module)) as song:
        frames = song.render(rate, seconds)
    return list(frames[0::2]), list(frames[1::2])


def render_note(patch, note: int, hold: float, tail: float,
                tick: float = REFERENCE_TICK, velocity: int = 127,
                key: Optional[int] = None):
    """Play one note. -> (left, right, hold actually used, rate)."""
    row = SPEED * tick
    hold_rows = max(1, int(round(hold / row)))
    hold_s = hold_rows * row
    rows = hold_rows + int(tail / row + 1.999) + 1
    module = probe_module(patch, note, hold_rows, rows, tick, velocity, key)
    left, right = render_module(module, hold_s + tail)
    return left, right, hold_s, SAMPLE_RATE


def _mono(left, right):
    return [(a + b) * 0.5 for a, b in zip(left, right)]


def probe(compiled: Compiled, genome=None, note: Optional[str] = None,
          octave: Optional[int] = None, velocity: int = 127,
          full: bool = False, use_cache: bool = True,
          tick: float = REFERENCE_TICK, key: Optional[int] = None):
    """Render, measure, validate. -> ProbeResult."""
    family = get_family(compiled.family)
    reg = genome.register if genome is not None and genome.register \
        else family.default_register
    name = note or reg[0]
    octv = reg[1] if octave is None else octave
    number = note_of(name, octv)
    hold = family.full_hold if full else family.quick_hold
    tail = family.tail
    cache_key = None
    if use_cache:
        cache_key = (json.dumps(compiled.patch.to_dict(), sort_keys=True,
                                default=str),
                     number, velocity, full, round(tick, 5), key)
        hit = _CACHE.get(cache_key)
        if hit is not None:
            measurement, level, patch_issues, label = hit
            dna = genome.dna if genome is not None else None
            # what was heard is cached; what it means depends on what the
            # genome asked for (a gated sound that rings is a warning only
            # if it was meant to be gated), so that is worked out each time
            issues = validate.check(
                measurement, dna, note_hz(number), rendered_ok=True,
                pitched=bool(compiled.style.get("pitched", family.pitched)),
                release_expected=compiled.style.get("release_expected",
                                                    True)) + list(patch_issues)
            return ProbeResult(measurement, issues, level, 0.0, True, label)
    started = time.time()
    hz = note_hz(number)
    pitched = bool(compiled.style.get("pitched", family.pitched))
    plain = family.plain(compiled) if full and pitched else None
    # Every dial but detune and motion is read from the plain voice.
    first = plain or compiled
    left, right, hold_s, rate = render_note(first.patch, number, hold, tail,
                                            tick, velocity, key)
    mono = _mono(left, right)
    measurement = descriptors.measure(mono, rate, hz, hold_s, tail,
                                      movement=full and pitched,
                                      sides=(left, right))
    measurement.pitched = pitched
    if plain is not None:
        left2, right2, _, _ = render_note(compiled.patch, number, hold, tail,
                                          tick, velocity, key)
        heard = descriptors.measure(_mono(left2, right2), rate, hz, hold_s,
                                    tail, movement=True, solo=measurement,
                                    sides=(left2, right2))
        for axis in ("detune", "motion"):
            measurement.axes[axis] = heard.axes.get(axis)
        for raw in ("am_slow", "am_fast", "beat_hz", "beat_depth", "pm_cents"):
            if raw in heard.raw:
                measurement.raw[f"ensemble_{raw}"] = heard.raw[raw]
        mono = _mono(left2, right2)
    level = dsp.window_rms(mono, rate, LEVEL_WINDOW)
    dna = genome.dna if genome is not None else None
    patch_issues = validate.check_patch(
        compiled.patch, patch_mod.built_samples(compiled.patch,
                                                (number, number)))
    issues = validate.check(measurement, dna, hz, rendered_ok=bool(mono),
                            pitched=pitched,
                            release_expected=compiled.style.get(
                                "release_expected", True)) + patch_issues
    result = ProbeResult(measurement, issues, level, time.time() - started,
                         False, f"{name}{octv}")
    if cache_key is not None:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.clear()
        _CACHE[cache_key] = (measurement, level, patch_issues, result.note)
    return result


def clear_cache():
    _CACHE.clear()


# -- any instrument of any module ---------------------------------------------------
def isolate(module: M.Module, number: int, note: int, hold_rows: int,
            rows: int, velocity: int = 127,
            tick: Optional[float] = None) -> M.Module:
    """A new module that plays only instrument `number` of `module`, one
    note: its envelopes and switches as they are, and just the sample that
    serves `note`. This is how an instrument nobody compiled from a genome
    (a recipe from a score, an instrument read out of a .it file) is
    measured by the same probe."""
    import copy
    source = module.instruments[number - 1]
    key = note - 1
    target, sample_no = source.note_map[key]
    if not sample_no:
        raise ValueError(f"instrument {number} has no sample for note "
                         f"{M.note_name(note)}")
    ins = copy.deepcopy(source)
    ins.note_map = [(n, 0) for n in range(120)]
    ins.note_map[key] = (target, 1)
    tempo = module.tempo if tick is None else int(round(2.5 / tick))
    out = M.Module(title="probe", speed=SPEED if tick is not None
                   else module.speed, tempo=tempo, mix_volume=48,
                   global_volume=128)
    out.samples.append(copy.copy(module.samples[sample_no - 1]))
    out.instruments.append(ins)
    pattern = M.Pattern(rows=max(M.MIN_ROWS, rows))
    cell = pattern.cell(0, 0)
    cell.note, cell.instrument = note, 1
    cell.volume = max(1, min(64, int(round(velocity / 127.0 * 64))))
    pattern.cell(hold_rows, 0).note = M.NOTE_OFF
    out.patterns.append(pattern)
    out.orders = [0]
    return out


def probe_instrument(module: M.Module, number: int, note: int,
                     full: bool = False, velocity: int = 127,
                     pitched: bool = True, hold: Optional[float] = None,
                     tail: float = 0.7, tick: Optional[float] = None,
                     dna=None):
    """Measure instrument `number` of any module at `note` (an IT note
    number). -> ProbeResult. The module's own tempo sets the tick unless
    `tick` is given; the unison and vibrato readings are those of the
    instrument as it is (there is no plain voice to compare it with)."""
    started = time.time()
    use_tick = tick if tick is not None else 2.5 / module.tempo
    speed = SPEED if tick is not None else module.speed
    row = speed * use_tick
    hold = hold if hold is not None else (2.4 if full else 0.84)
    hold_rows = max(1, int(round(hold / row)))
    hold_s = hold_rows * row
    rows = hold_rows + int(tail / row + 1.999) + 1
    probe_mod = isolate(module, number, note, hold_rows, rows, velocity, tick)
    left, right = render_module(probe_mod, hold_s + tail)
    mono = _mono(left, right)
    hz = note_hz(note)
    measurement = descriptors.measure(mono, SAMPLE_RATE, hz, hold_s, tail,
                                      movement=full and pitched,
                                      sides=(left, right))
    measurement.pitched = pitched
    level = dsp.window_rms(mono, SAMPLE_RATE, LEVEL_WINDOW)
    issues = validate.check(measurement, dna, hz, rendered_ok=bool(mono),
                            pitched=pitched)
    return ProbeResult(measurement, issues, level, time.time() - started,
                       False, M.note_name(note))
