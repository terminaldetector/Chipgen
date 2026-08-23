"""audition.py — measure what a patch actually sounds like, so it can be chosen.

The problem this exists to solve: a model told "write something aggressive"
picks an instrument by the sound of its NAME. `techno_bass` sounds
aggressive; whether it is aggressive is a separate question that nobody
asks, and the answer changes the moment somebody imports a bank from a VGM
where the names are `bass_fat_bass_1`. Genre tags do not fix this — they
move the guess from the patch name to the tag, and a tag cannot be checked.

So every patch is rendered and measured, and selection happens over the
measurements. Then "aggressive bass" means something a program can
evaluate: bright for its register, fast attack, harmonically dense. A
patch imported five minutes ago gets the same treatment as a built-in one,
and neither needs a human to have described it.

Measurements are taken on the YM3438 core. The discrete YM2612's resistor
ladder outputs 0.01538 RMS with no note playing, which is louder than
several real patches and would put a floor under every number here.

Three axes are pitch-independent on purpose, because a patch has to be
comparable across the notes it gets played at:

- **brightness** is the spectral centroid divided by the fundamental —
  "which harmonic is the centre of mass" — not the centroid in Hz, which
  simply rises with the note.
- **velocity response** is the dB spread from velocity 16 to 127, which is
  how much dynamic range the patch actually gives you. Several patches on
  this chip are nearly flat, and a score written expecting dynamics from
  one of those has no dynamics.
- **attack** and **decay** are in seconds, measured off the envelope.
"""

import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import analysis
import audio
import instruments as instruments_mod
import opn2

#: Notes the matrix is measured at — two octaves apart each, spanning the
#: range these patches get used across. C2 is bass territory, C5 lead.
DEFAULT_NOTES = (("C", 2), ("C", 3), ("C", 4), ("C", 5))
#: Velocities: floor, middle, full. The corpus median is 21 of 127, so the
#: quiet end is where most notes actually live and is not a corner case.
DEFAULT_VELOCITIES = (16, 64, 127)
#: How long to hold, and how long to listen after release.
#:
#: The tail is 1.2 s and that number is measured, not chosen for comfort.
#: At 0.5 s the longest patch in the bank never reached the -20 dB point
#: inside the window, so its release came back as None, got dropped from
#: the average, and the bank reported strings at 494 ms when it is really
#: 904 ms. A measurement window that silently truncates does not report an
#: error, it reports a wrong number. Nothing in the bank changes past 1.0 s.
HOLD_SECONDS = 0.6
RELEASE_SECONDS = 1.2
#: A patch quieter than this at full velocity cannot carry a part.
AUDIBLE_PEAK = 0.01                      # -40 dBFS


def _render(instrument, note, octave, velocity, hold=HOLD_SECONDS,
            tail=RELEASE_SECONDS, chip_type="ym3438"):
    chip = opn2.YM2612(chip_type=chip_type)
    chip.set_instrument(0, instrument)
    chip.set_pan(0, True, True)
    # Velocity through note_on, not set_volume: note_on is the path a score
    # actually takes, and it folds in the patch's calibration trim. Measuring
    # any other way measures something the music never hears.
    chip.note_on(0, note, octave, velocity=velocity)
    held = chip.render(int(chip.native_rate * hold))
    chip.note_off(0)
    released = chip.render(int(chip.native_rate * tail))
    rate = chip.native_rate
    chip.close()
    return audio.concat([held, released]), held, released, rate


def measure_one(instrument, note, octave, velocity):
    """Measure a single note. -> dict of measurements, all floats or None."""
    whole, held, released, rate = _render(instrument, note, octave, velocity)
    mono_held = analysis.to_mono(held)
    peak = audio.peak(whole)
    if peak <= 0.0:
        return {"peak": 0.0, "rms": 0.0, "peak_dbfs": float("-inf"),
                "fundamental": None, "cents_error": None, "attack": None,
                "decay": None, "release": None, "brightness": None,
                "harmonics": 0, "silent": True,
                "low": 0.0, "mid": 0.0, "high": 0.0}

    wanted = opn2.note_to_freq(note, octave)
    measured = analysis.fundamental(mono_held, rate)
    centroid = analysis.spectral_centroid(mono_held, rate)
    bands = analysis.band_energy(mono_held, rate)
    # Three coarse bands on top of the octave detail: how much of this
    # patch lands under 200 Hz, in the 200 Hz - 2 kHz midrange where leads
    # and vocals fight, and above 2 kHz. This is what decides whether two
    # parts will mask each other, which no single "brightness" number says.
    low = bands["sub"] + bands["31"] + bands["62"] + bands["125"]
    mid = bands["250"] + bands["500"] + bands["1k"]
    high = bands["2k"] + bands["4k"] + bands["8k+"]
    return {
        "low": low, "mid": mid, "high": high,
        "peak": peak,
        "rms": audio.rms(whole),
        "peak_dbfs": analysis.dbfs(peak),
        "requested_hz": wanted,
        "fundamental": measured,
        "cents_error": (analysis.cents(measured, wanted)
                        if measured and wanted else None),
        "attack": analysis.attack_seconds(mono_held, rate),
        "decay": analysis.decay_seconds(mono_held, rate),
        "release": analysis.decay_seconds(analysis.to_mono(released), rate),
        "centroid_hz": centroid,
        # Pitch-independent: which harmonic the energy centres on.
        "brightness": (centroid / measured) if measured else None,
        "harmonics": analysis.harmonic_count(mono_held, rate, measured),
        "silent": peak < AUDIBLE_PEAK,
    }


def audition(name, notes=DEFAULT_NOTES, velocities=DEFAULT_VELOCITIES) -> dict:
    """Measure one patch across the note/velocity matrix. -> report dict."""
    instrument = (name if hasattr(name, "operators")
                  else instruments_mod.get(name))
    label = getattr(instrument, "name", str(name))
    cells = []
    for note, octave in notes:
        for velocity in velocities:
            cell = measure_one(instrument, note, octave, velocity)
            cell.update({"note": f"{note}-{octave}", "velocity": velocity})
            cells.append(cell)

    full = [c for c in cells if c["velocity"] == max(velocities)]
    sounding = [c for c in full if not c["silent"]]
    pitched = [c for c in cells if c.get("cents_error") is not None]

    loud = [c for c in cells if c["velocity"] == max(velocities)]
    quiet = [c for c in cells if c["velocity"] == min(velocities)]
    velocity_db = None
    if loud and quiet:
        a, b = _mean(c["peak"] for c in loud), _mean(c["peak"] for c in quiet)
        if a > 0 and b > 0:
            velocity_db = analysis.dbfs(a) - analysis.dbfs(b)

    return {
        "name": label,
        "algorithm": instrument.algorithm,
        "feedback": instrument.feedback,
        "carriers": [opn2._LIST_TO_OP[i] for i in instrument.carrier_indices()],
        "carrier_tl": [instrument.operators[i].total_level
                       for i in instrument.carrier_indices()],
        "trim": getattr(instrument, "trim", 0),
        "character": instruments_mod.CHARACTER.get(label, ""),
        "peak_dbfs": (max(c["peak_dbfs"] for c in full)
                      if full else float("-inf")),
        "attack": _mean(c["attack"] for c in sounding if c["attack"] is not None),
        "decay": _mean(c["decay"] for c in sounding if c["decay"] is not None),
        "release": _mean(c["release"] for c in sounding
                         if c["release"] is not None),
        "brightness": _mean(c["brightness"] for c in sounding
                            if c["brightness"] is not None),
        "harmonics": _mean(c["harmonics"] for c in sounding),
        # Band balance measured low on the keyboard, where a part competes
        # for the bottom of the mix, and high, where leads live.
        "low_at_c2": _first(c["low"] for c in cells
                            if c["note"] == "C-2" and c["velocity"] == 127),
        "mid_at_c4": _first(c["mid"] for c in cells
                            if c["note"] == "C-4" and c["velocity"] == 127),
        "high_at_c4": _first(c["high"] for c in cells
                             if c["note"] == "C-4" and c["velocity"] == 127),
        "velocity_range_db": velocity_db,
        "worst_cents_error": (max((abs(c["cents_error"]) for c in pitched),
                                  default=None)),
        "audible_notes": [c["note"] for c in full if not c["silent"]],
        "silent_notes": [c["note"] for c in full if c["silent"]],
        "usable": bool(sounding),
        "cells": cells,
    }


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _first(values):
    return next((v for v in values if v is not None), None)


def audition_bank(names=None, notes=DEFAULT_NOTES,
                  velocities=DEFAULT_VELOCITIES, progress=None) -> dict:
    """Measure the whole bank. -> {name: report}."""
    names = list(names or instruments_mod.names())
    out = {}
    for i, name in enumerate(names, 1):
        if progress:
            progress(i, len(names), name)
        out[name] = audition(name, notes, velocities)
    return out


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def format_one(report: dict) -> str:
    r = report
    lines = [
        f"patch:       {r['name']}",
        f"algorithm:   {r['algorithm']}  feedback {r['feedback']}  "
        f"carriers op{r['carriers']} at TL {r['carrier_tl']}",
    ]
    if r["character"]:
        lines.append(f"character:   {r['character']}")
    lines += [
        f"peak:        {r['peak_dbfs']:.1f} dBFS at velocity 127",
        f"attack:      {_ms(r['attack'])}      "
        f"decay(-20dB): {_ms(r['decay'])}      release: {_ms(r['release'])}",
        f"brightness:  {_num(r['brightness'], 'x fundamental')}"
        f"   harmonics: {_num(r['harmonics'], '')}",
        f"velocity:    {_num(r['velocity_range_db'], 'dB from v16 to v127')}",
        f"pitch:       worst error {_num(r['worst_cents_error'], 'cents')}",
    ]
    if r["silent_notes"]:
        lines.append(f"SILENT at:   {', '.join(r['silent_notes'])}")
    lines.append("")
    lines.append(f"  {'note':6s} {'vel':>4s} {'peak dBFS':>10s} "
                 f"{'f0 Hz':>9s} {'cents':>7s} {'attack':>8s} {'bright':>7s}")
    for c in r["cells"]:
        lines.append(
            f"  {c['note']:6s} {c['velocity']:4d} {c['peak_dbfs']:10.1f} "
            f"{_col(c['fundamental'], 9, 2)} {_col(c['cents_error'], 7, 1)} "
            f"{_ms(c['attack']):>8s} {_col(c['brightness'], 7, 2)}")
    return "\n".join(lines)


def format_bank(reports: dict) -> str:
    header = (f"{'patch':18s} {'peak':>7s} {'atk':>7s} {'rel':>7s} "
              f"{'bright':>7s} {'harm':>5s} {'vel dB':>7s} {'role':>10s}")
    lines = [header, "-" * len(header)]
    for name, r in sorted(reports.items()):
        lines.append(
            f"{name:18s} {r['peak_dbfs']:7.1f} {_ms(r['attack']):>7s} "
            f"{_ms(r['release']):>7s} {_col(r['brightness'], 7, 2)} "
            f"{_col(r['harmonics'], 5, 1)} "
            f"{_col(r['velocity_range_db'], 7, 1)} "
            f"{','.join(suggest_roles(r)) or '-':>10s}")
    return "\n".join(lines)


def _ms(seconds):
    return "-" if seconds is None else f"{seconds * 1000:.0f}ms"


def _num(value, unit):
    return "-" if value is None else f"{value:.2f} {unit}".strip()


def _col(value, width, places):
    return ("-".rjust(width) if value is None
            else f"{value:{width}.{places}f}")


# --------------------------------------------------------------------------
# Derived taxonomy — labels come from the measurements, not from the name
# --------------------------------------------------------------------------
#: Thresholds are stated here rather than buried, because they are
#: judgement calls over measured quantities and someone will want to argue
#: with them. Brightness is in multiples of the fundamental: 1.0 means all
#: the energy is in the first harmonic (a sine), 6.0 means the centre of
#: mass sits six harmonics up (a bright, buzzy timbre).
BRIGHT_DARK, BRIGHT_BRIGHT = 2.0, 5.0
ATTACK_FAST, ATTACK_SLOW = 0.010, 0.060          # seconds
RELEASE_SHORT, RELEASE_LONG = 0.120, 0.400       # seconds


def brightness_label(report) -> str:
    b = report.get("brightness")
    if b is None:
        return "unknown"
    if b < BRIGHT_DARK:
        return "dark"
    return "bright" if b > BRIGHT_BRIGHT else "medium"


def attack_label(report) -> str:
    a = report.get("attack")
    if a is None:
        return "unknown"
    if a <= ATTACK_FAST:
        return "sharp"
    return "soft" if a >= ATTACK_SLOW else "medium"


def sustain_label(report) -> str:
    r = report.get("release")
    if r is None:
        return "unknown"
    if r <= RELEASE_SHORT:
        return "short"
    return "long" if r >= RELEASE_LONG else "medium"


def suggest_roles(report):
    """Which musical jobs the measurements say this patch can do.

    Deliberately permissive — this narrows the field, it does not pick.
    A patch can be both a pluck and a lead; the score decides.
    """
    roles = []
    bright, attack, sustain = (brightness_label(report), attack_label(report),
                               sustain_label(report))
    if not report.get("usable"):
        return roles
    low_notes = [c for c in report["cells"]
                 if c["note"] in ("C-2", "C-3") and not c["silent"]]
    high_notes = [c for c in report["cells"]
                  if c["note"] in ("C-4", "C-5") and not c["silent"]]
    if low_notes and bright in ("dark", "medium"):
        roles.append("bass")
    if high_notes and attack in ("sharp", "medium") and bright != "dark":
        roles.append("lead")
    if high_notes and attack == "soft" and sustain in ("medium", "long"):
        roles.append("pad")
    if attack == "sharp" and sustain == "short":
        roles.append("pluck")
    if high_notes and sustain in ("medium", "long") and bright != "bright":
        roles.append("harmony")
    return roles


def summarise(report: dict) -> dict:
    """The compact form: what selection and prompts read."""
    return {
        "name": report["name"],
        "algorithm": report["algorithm"],
        "peak_dbfs": report["peak_dbfs"],
        "brightness": report["brightness"],
        "brightness_label": brightness_label(report),
        "attack": report["attack"],
        "attack_label": attack_label(report),
        "release": report["release"],
        "sustain_label": sustain_label(report),
        "harmonics": report["harmonics"],
        "low_at_c2": report.get("low_at_c2"),
        "mid_at_c4": report.get("mid_at_c4"),
        "high_at_c4": report.get("high_at_c4"),
        "worst_cents_error": report.get("worst_cents_error"),
        "velocity_range_db": report["velocity_range_db"],
        "roles": suggest_roles(report),
        "audible_notes": report["audible_notes"],
        "silent_notes": report["silent_notes"],
        "usable": report["usable"],
    }


# --------------------------------------------------------------------------
# Cache — measuring the bank costs real seconds, and nothing in it moves
# --------------------------------------------------------------------------
CACHE_PATH = os.path.join(_HERE, "bank_audition.json")


def load_cache(path: str = CACHE_PATH):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def save_cache(reports: dict, path: str = CACHE_PATH):
    compact = {name: summarise(r) for name, r in reports.items()}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(compact, handle, indent=1, sort_keys=True)
    return path


def characteristics(refresh: bool = False):
    """Summarised measurements for the whole bank, cached on disk."""
    if not refresh:
        cached = load_cache()
        if cached and set(cached) >= set(instruments_mod.names()):
            return cached
    reports = audition_bank()
    save_cache(reports)
    return {name: summarise(r) for name, r in reports.items()}


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(
        description="measure what a patch sounds like")
    parser.add_argument("patch", nargs="?",
                        help="patch name; omit to measure the whole bank")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--write-cache", action="store_true",
                        help="refresh bank_audition.json")
    args = parser.parse_args(argv)

    if args.patch:
        report = audition(args.patch)
        print(json.dumps(report, indent=1) if args.json
              else format_one(report))
        return 0

    def progress(i, total, name):
        if not args.json:
            print(f"\rmeasuring {i}/{total} {name:20s}", end="", file=sys.stderr)
    reports = audition_bank(progress=progress)
    if not args.json:
        print("\r" + " " * 40 + "\r", end="", file=sys.stderr)
    if args.write_cache:
        print(f"wrote {save_cache(reports)}", file=sys.stderr)
    print(json.dumps({n: summarise(r) for n, r in reports.items()},
                     indent=1, sort_keys=True) if args.json
          else format_bank(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
