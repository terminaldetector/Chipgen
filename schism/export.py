"""export.py — what a built module is, written down.

A module is bytes. The people and programs that use one also need to know
which key plays which drum, where each sample loops, what an instrument was
made from and, for a voice lifted from another chip, the operator numbers it
came from. That lives here: a key map for the report, a sidecar `.json` that
sits beside the `.it`, and the presets that hold a module to what a console
could have played.

Nothing in this file changes what a module sounds like except a preset, and
a preset says what it changed.
"""

import json
from array import array
from typing import Dict, List, Optional, Tuple

from . import model as M

SIDECAR_FORMAT = "schism-sidecar/1"

_NNA = ("cut", "cont", "off", "fade")
_DCT = ("off", "note", "sample", "inst")
_DCA = ("cut", "off", "fade")


class ExportError(ValueError):
    """A module that a preset will not take, with every reason."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# -- which key plays what --------------------------------------------------------
def key_runs(ins: M.Instrument) -> List[Tuple[int, int, int]]:
    """[(first note, last note, sample number)]: runs of neighbouring keys
    that play one sample. Notes are 1-based, as `Cell.note`; keys that play
    nothing are left out."""
    runs: List[list] = []
    for index, (_, sample) in enumerate(ins.note_map):
        note = index + 1
        if not sample:
            continue
        if runs and runs[-1][2] == sample and runs[-1][1] == note - 1:
            runs[-1][1] = note
        else:
            runs.append([note, note, sample])
    return [tuple(r) for r in runs]


def is_kit(ins: M.Instrument) -> bool:
    """A kit puts a different sample on (nearly) every key; a pitched
    instrument puts one sample on an octave or more."""
    runs = key_runs(ins)
    return (len({r[2] for r in runs}) >= 2
            and max(r[1] - r[0] + 1 for r in runs) <= 3)


def _sample_name(module: M.Module, number: int) -> str:
    if 1 <= number <= len(module.samples):
        return module.samples[number - 1].name or f"sample {number}"
    return f"sample {number}"


def _range_text(first: int, last: int) -> str:
    return M.note_name(first) if first == last \
        else f"{M.note_name(first)}..{M.note_name(last)}"


def kit_lines(module: M.Module) -> Tuple[str, ...]:
    """One line per kit: its keys, in the order the keyboard has them. The
    key map used to be something you learnt by playing a wrong key."""
    lines = []
    for number, ins in enumerate(module.instruments, 1):
        if not is_kit(ins):
            continue
        keys = ", ".join(f"{_range_text(a, b)} {_sample_name(module, s)}"
                         for a, b, s in key_runs(ins))
        lines.append(f"instrument {number:02d} {ins.name or 'kit'}: {keys}")
    return tuple(lines)


# -- who plays what --------------------------------------------------------------
def channel_instruments(module: M.Module) -> Dict[int, set]:
    """channel (0-based) -> the instrument numbers it plays a note with,
    following the order list the way a player does."""
    current: Dict[int, int] = {}
    played: Dict[int, set] = {}
    for order in module.orders:
        if order >= len(module.patterns):
            continue
        for row in module.patterns[order].cells:
            for channel, cell in enumerate(row):
                if cell.instrument:
                    current[channel] = cell.instrument
                if 1 <= cell.note <= 120 and channel in current:
                    played.setdefault(channel, set()).add(current[channel])
    return played


# -- the sidecar -----------------------------------------------------------------
def _loop(span, pingpong) -> Optional[dict]:
    if span is None:
        return None
    return {"begin": span[0], "end": span[1],
            "mode": "pingpong" if pingpong else "forward"}


def _sample_dict(number: int, sample: M.Sample) -> dict:
    return {"number": number, "name": sample.name, "frames": sample.frames,
            "bits": sample.bits,
            "channels": 2 if getattr(sample, "right", None) is not None else 1,
            "c5speed": sample.c5speed, "volume": sample.volume,
            "loop": _loop(sample.loop, sample.loop_pingpong),
            "sustain_loop": _loop(sample.sustain_loop,
                                  sample.sustain_pingpong)}


def _instrument_dict(module: M.Module, number: int, ins: M.Instrument) -> dict:
    runs = key_runs(ins)
    out = {
        "number": number, "name": ins.name, "kit": is_kit(ins),
        "nna": _NNA[ins.nna], "dct": _DCT[ins.dct], "dca": _DCA[ins.dca],
        "fadeout": ins.fadeout, "gain": ins.global_volume,
        "pan": ins.default_pan,
        "filter": None if ins.cutoff is None else {
            "cutoff": ins.cutoff, "resonance": ins.resonance or 0,
            "envelope": bool(ins.pitch_envelope
                             and ins.pitch_envelope.filter)},
        "envelopes": {
            "volume": bool(ins.volume_envelope and ins.volume_envelope.nodes),
            "pan": bool(ins.pan_envelope and ins.pan_envelope.nodes),
            "pitch": bool(ins.pitch_envelope and ins.pitch_envelope.nodes
                          and not ins.pitch_envelope.filter)},
        "samples": sorted({r[2] for r in runs}),
        "keys": [{"from": M.note_name(a), "to": M.note_name(b), "sample": s,
                  "sample_name": _sample_name(module, s)}
                 for a, b, s in runs]}
    recipe = (getattr(module, "recipes", None) or {}).get(number)
    if recipe:
        out["recipe"] = recipe
    forged = (getattr(module, "forged", None) or {}).get(number)
    if forged:                         # (Genome, Compiled), as notation_hook has it
        genome, compiled = forged
        out["forge"] = {"family": compiled.family, "name": compiled.name,
                        "genome": genome.to_dict()}
    dump = (getattr(module, "fm", None) or {}).get(number)
    if dump:
        out["operators"] = dump
    return out


def sidecar(module: M.Module, file: str = "", peak=None,
            preset: Optional[str] = None, notes=()) -> dict:
    """Everything a person or a program needs to know about a module and
    cannot read off the bytes without parsing them. JSON-able."""
    return {
        "format": SIDECAR_FORMAT,
        "module": {
            "file": file, "title": module.title,
            "channels_used": module.channels_used(),
            "speed": module.speed, "tempo": module.tempo,
            "seconds": round(module.seconds(), 2),
            "mix_volume": module.mix_volume,
            "global_volume": module.global_volume,
            "pan_separation": module.pan_separation,
            "patterns": len(module.patterns), "orders": len(module.orders),
            "peak": None if peak is None else {
                "before": round(peak.before, 3),
                "after": round(peak.after, 3),
                "target": peak.target,
                "mix_volume": [peak.mix_before, peak.mix_after],
                "engine": "libopenmpt"}},
        "preset": preset,
        "notes": list(notes),
        "instruments": [_instrument_dict(module, n, ins)
                        for n, ins in enumerate(module.instruments, 1)
                        if ins.name or key_runs(ins)],
        "samples": [_sample_dict(n, s)
                    for n, s in enumerate(module.samples, 1)],
    }


def sidecar_path(module_path: str) -> str:
    """`song.it` -> `song.it.json`: beside the module, and named for it, so
    a `song.json` of somebody else's is never overwritten."""
    return module_path + ".json"


def write_sidecar(path: str, data: dict) -> str:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    return path


# -- presets -----------------------------------------------------------------------
def _eight_bit(module: M.Module) -> int:
    """Quantise every 16-bit sample to 8 bit (rounding, no dither); -> how
    many samples changed. Samples that share data share the result."""
    done: Dict[int, array] = {}
    changed = 0
    for sample in module.samples:
        if sample.bits != 16 or sample.data is None:
            continue
        for attr in ("data", "right"):
            data = getattr(sample, attr, None)
            if data is None:
                continue
            key = id(data)
            if key not in done:
                done[key] = array("b", (max(-128, min(127, (v + 128) >> 8))
                                        for v in data))
            setattr(sample, attr, done[key])
        sample.bits = 8
        changed += 1
    return changed


def _no_filters(module: M.Module) -> int:
    changed = 0
    for ins in module.instruments:
        if ins.cutoff is not None or ins.resonance is not None:
            ins.cutoff = ins.resonance = None
            changed += 1
        if ins.pitch_envelope is not None and ins.pitch_envelope.filter:
            ins.pitch_envelope = None
    return changed


def _duty_comment(module: M.Module) -> List[str]:
    """The duty cycle of every pulse instrument, as lines for the song
    message (IT calls the message its comment): the file has nowhere else to
    keep a number the NES keeps in a register."""
    lines = []
    for number, recipe in sorted((getattr(module, "recipes", None)
                                  or {}).items()):
        if recipe.get("wave") in ("pulse", "square"):
            duty = recipe.get("params", {}).get("duty", 0.5)
            name = module.instruments[number - 1].name or f"inst {number}"
            lines.append(f"duty {number:02d} {name}: {duty:g}")
    return lines


def _preset_schism(module: M.Module):
    return []


def _preset_nes(module: M.Module):
    problems, notes = [], []
    used = module.channels_used()
    if used > 4:
        problems.append(
            f"the module uses {used} channels; the NES has four "
            f"(two pulses, a triangle and noise)")
    if problems:
        raise ExportError(problems)
    count = _eight_bit(module)
    if count:
        notes.append(f"{count} samples quantised to 8 bit")
    count = _no_filters(module)
    if count:
        notes.append(f"filter taken off {count} instruments (the NES has none)")
    duty = _duty_comment(module)
    if duty:
        module.message = (module.message.rstrip("\n") + "\n" if module.message
                          else "") + "\n".join(duty)
        notes.append("duty cycles written into the song message")
    return notes


def _preset_sega(module: M.Module):
    problems, notes = [], []
    used = module.channels_used()
    if used > 7:
        problems.append(
            f"the module uses {used} channels; the Genesis has six FM voices "
            f"and one DAC channel for drums (seven)")
    kits = {n for n, ins in enumerate(module.instruments, 1) if is_kit(ins)}
    drum_channels = sorted(c for c, played in channel_instruments(module).items()
                           if played & kits)
    if len(drum_channels) > 1:
        problems.append(
            "drums are on channels "
            + ", ".join(str(c + 1) for c in drum_channels)
            + "; the Genesis plays them through one DAC channel")
    if problems:
        raise ExportError(problems)
    dumps = getattr(module, "fm", None) or {}
    for number, ins in enumerate(module.instruments, 1):
        if number in kits or not (ins.name or key_runs(ins)):
            continue
        if number not in dumps:
            notes.append(f"instrument {number:02d} has no operator dump (it "
                         f"was not made by `forge lift-voice`): the sidecar "
                         f"holds its PCM description only")
    if dumps:
        notes.append(f"{len(dumps)} instruments carry operator dumps in the "
                     f"sidecar")
    return notes


PRESETS = {
    "schism": (_preset_schism, "the full format: 16-bit, loops, stereo, NNA; "
               "nothing is taken away"),
    "nes": (_preset_nes, "at most 4 channels, 8-bit samples, no filter; "
            "pulse duty cycles go into the song message"),
    "sega": (_preset_sega, "at most 7 channels and drums on one of them; "
             "operator dumps of lifted voices go into the sidecar"),
}


def apply_preset(module: M.Module, name: str) -> List[str]:
    """Hold `module` to a console's limits, in place. -> what it changed.
    Raises `ExportError` naming every limit the module is over."""
    try:
        function = PRESETS[name][0]
    except KeyError:
        raise ExportError([f"no preset {name!r}; have: "
                           f"{', '.join(sorted(PRESETS))}"]) from None
    return function(module)
