"""probe.py — play one note through the real emulator and read it back.

A probe is one note, held and then released, rendered by the same chip
object the sequencer would drive (not an approximation of it) and run
through descriptors.measure. It is the only place the search touches audio,
and it is cached: two genomes that compile to the same instrument cost one
render.

`full=True` holds the note 2.4 s (detune needs a second of steady note
after the attack and the fall); the default quick probe holds it 0.8 s and
skips the detune and motion readings. The search runs quick probes and gives
finalists a full one.

An instrument with a detune layer is rendered twice on a full probe: the
main voice alone for every dial but detune (two voices beating shift the
level at whatever moment it is read, and the envelope dials would follow
the beat), and the whole ensemble for detune.
"""

import math
import time
from dataclasses import dataclass, field

import opn2

from . import descriptors, validate
from .backends import get_backend

_CACHE = {}
_CACHE_LIMIT = 4096
#: Seconds the key is down. A quick probe is long enough for every dial but
#: detune and motion; the full one is long enough for a one-hertz beat.
QUICK_HOLD = 0.8
FULL_HOLD = 2.4


@dataclass
class ProbeResult:
    measurement: descriptors.Measurement
    issues: list
    level: float = 0.0               # loudest 300 ms, RMS
    seconds: float = 0.0             # what the probe cost
    cached: bool = False
    note: str = ""

    @property
    def axes(self):
        return self.measurement.axes

    @property
    def ok(self) -> bool:
        return not validate.errors(self.issues)


def note_frequency(note: str, octave: int) -> float:
    return opn2.note_to_freq(note, octave)


def window_rms(mono, rate: float, seconds: float = 0.3) -> float:
    """Loudest `seconds` of a signal, as RMS: the loudness measure the
    bank was calibrated with (see calibrate_bank.py)."""
    n = len(mono)
    if n == 0:
        return 0.0
    width = min(n, max(1, int(seconds * rate)))
    energy = sum(v * v for v in mono[:width])
    best = energy
    for i in range(width, n):
        energy += mono[i] * mono[i] - mono[i - width] * mono[i - width]
        if energy > best:
            best = energy
    return math.sqrt(max(0.0, best) / width)


def probe(backend, compiled, genome=None, note: str = None, octave: int = None,
          velocity: int = 127, full: bool = False, use_cache: bool = True):
    """Render, measure, validate. `backend` is a Backend or its name."""
    if isinstance(backend, str):
        backend = get_backend(backend)
    reg = genome.register if genome is not None else backend.default_register
    note = note or reg[0]
    octave = reg[1] if octave is None else octave
    hold, tail = (FULL_HOLD, 0.5) if full else (QUICK_HOLD, 0.5)
    key = None
    if use_cache:
        key = (backend.name, repr(backend.to_dict(compiled)), note, octave,
               velocity, full)
        hit = _CACHE.get(key)
        if hit is not None:
            return ProbeResult(hit.measurement, hit.issues, hit.level, 0.0,
                               True, hit.note)
    started = time.time()
    hz = note_frequency(note, octave)
    pitched = compiled.style.get("pitched", True)
    layered = hasattr(backend, "voices_for") and backend.voices_for(compiled) > 1
    # Every dial but detune is read from the main voice alone: two voices
    # beating shift the level at whatever moment it is read, and attack,
    # body and decay would follow the beat instead of the envelope.
    mono, rate = backend.render(compiled, note, octave, velocity, hold, tail,
                                ensemble=False)
    measurement = descriptors.measure(mono, rate, hz, hold, tail,
                                      movement=full and pitched)
    measurement.pitched = pitched
    if layered and full and pitched:
        together, _ = backend.render(compiled, note, octave, velocity, hold,
                                     tail, ensemble=True)
        heard = descriptors.measure(together, rate, hz, hold, tail,
                                    movement=True, solo=measurement)
        measurement.axes["detune"] = heard.axes.get("detune")
        for key in ("am_slow", "beat_hz", "beat_depth"):
            if key in heard.raw:
                measurement.raw[f"ensemble_{key}"] = heard.raw[key]
    level = window_rms(mono, rate)
    dna = genome.dna if genome is not None else None
    issues = validate.check(measurement, backend, dna, hz,
                            rendered_ok=bool(mono), pitched=pitched)
    result = ProbeResult(measurement, issues, level,
                         time.time() - started, False, f"{note}{octave}")
    if key is not None:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.clear()
        _CACHE[key] = result
    return result


def clear_cache():
    _CACHE.clear()
