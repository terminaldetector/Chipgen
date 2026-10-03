"""
calibrate_bank.py — measure every patch and regenerate its loudness trim.

    python3 python/calibrate_bank.py           # measure and report
    python3 python/calibrate_bank.py --write   # regenerate bank_calibration.py

Patches designed by ear land wherever they land. Before this existed the
bank spanned 15.4 dB from orch_hit to distorted_lead, which meant swapping
one lead for another silently rebalanced the whole arrangement and made
"pick an instrument by name" a much worse deal than it looks.

The fix is not to redesign the patches — their operator settings are the
timbre, and nudging Total Level to even out loudness would change how the
modulators bite. It is to carry a separate per-patch trim that the chip
layer adds to the CARRIERS only, which moves level without touching
timbre, and to derive that number by measurement rather than by taste.

Method: render each patch playing the same note for the same time through
the real render path, measure it, and solve for the trim that lands it on
the bank's median.

The measurement is the MAXIMUM RMS over a sliding 300 ms window, not the
RMS of the whole render. Plain RMS is unfair to anything percussive: an
orchestra hit is a short stab followed by silence, and averaging the
silence in reports it as 16 dB quieter than a sustained lead when it is
not quieter at all — it is shorter. A sliding window is what loudness
meters use, for exactly this reason.

Median rather than max because Total Level can only attenuate: a patch is
boosted by REMOVING attenuation, and only as far as its quietest carrier's
headroom to TL 0 allows. Targeting the median means roughly half the bank
is boosted and half attenuated, so the trims stay small and nothing runs
out of room.

Rerun after adding a patch, changing a patch, or changing anything in the
render path that affects level (the mixer, the chip scaling, the resampler).
"""

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import audio
import instruments as instruments_mod
from events import End, FMInstrumentSelect, FMNoteOff, FMNoteOn, Wait
from sequencer import Sequencer

OUTPUT_PATH = os.path.join(_HERE, "bank_calibration.py")

#: One Total Level step is 0.75 dB — the resolution of any trim we can apply.
TL_STEP_DB = 0.75
#: Measurement note. A3 sits in the middle of where these patches get used,
#: and key scaling makes the answer note-dependent, so it has to be fixed.
NOTE, OCTAVE = "A", 3
HOLD_TICKS, TAIL_TICKS = 144, 48
#: Loudness window, in seconds. Long enough to average a few cycles of a
#: bass note, short enough that a 200 ms stab fills it.
WINDOW_SECONDS = 0.3


def window_rms(buf, sample_rate: int) -> float:
    """Loudest 300 ms of a buffer, as RMS.

    Runs on a running sum so it stays linear, and reads frames through the
    buffer protocol so it works with or without numpy — this is a dev tool,
    but it is a dev tool that has to agree with what the engine renders.
    """
    frames = len(buf)
    if frames == 0:
        return 0.0
    width = min(frames, max(1, int(WINDOW_SECONDS * sample_rate)))

    def energy(index):
        frame = buf[index]
        if isinstance(frame, (int, float)):
            return float(frame) ** 2
        return sum(float(v) ** 2 for v in frame) / len(frame)

    total = sum(energy(i) for i in range(width))
    best = total
    for i in range(width, frames):
        total += energy(i) - energy(i - width)
        if total > best:
            best = total
    return math.sqrt(max(0.0, best) / width)


def measure(name: str, seq: Sequencer, trim: int = None) -> float:
    """Loudness of one patch playing one note. `trim` overrides the stored one."""
    patch = instruments_mod.BANK[name]
    original = patch.trim
    if trim is not None:
        patch.trim = trim
    try:
        events = [FMInstrumentSelect(channel=0, instrument=name),
                  FMNoteOn(channel=0, note=NOTE, octave=OCTAVE),
                  Wait(ticks=HOLD_TICKS),
                  FMNoteOff(channel=0),
                  Wait(ticks=TAIL_TICKS),
                  End()]
        return window_rms(seq.render(events), seq.target_rate)
    finally:
        patch.trim = original


def calibrate(verbose: bool = True):
    seq = Sequencer()
    names = instruments_mod.names()

    # Measure with trims disabled, so a rerun converges on the same answer
    # instead of drifting by whatever the last run applied.
    raw = {name: measure(name, seq, trim=0) for name in names}
    ordered = sorted(raw.values())
    target = ordered[len(ordered) // 2]

    trims = {}
    for name in names:
        level = raw[name]
        if level <= 0:
            trims[name] = 0
            continue
        wanted = 20.0 * math.log10(level / target) / TL_STEP_DB
        steps = int(round(wanted))
        # Boosting means removing attenuation, and a carrier cannot go below
        # Total Level 0. Clamp to what the patch actually has spare.
        headroom = instruments_mod.BANK[name].headroom()
        trims[name] = max(-headroom, min(127, steps))

    if verbose:
        print(f"target (median): rms {target:.4f}\n")
        print(f"{'патч':16s} {'до, дБ':>8s} {'trim':>6s} {'после, дБ':>10s} {'зазор':>6s}")
        after = {}
        for name in names:
            for patch_name, value in trims.items():
                instruments_mod.BANK[patch_name].trim = value
            after[name] = measure(name, seq)
        for name in names:
            before_db = 20 * math.log10(raw[name] / target)
            after_db = 20 * math.log10(after[name] / target) if after[name] > 0 else 0.0
            print(f"{name:16s} {before_db:+8.1f} {trims[name]:+6d} {after_db:+10.1f} "
                  f"{instruments_mod.BANK[name].headroom():6d}")
        spread_before = 20 * math.log10(max(raw.values()) / min(raw.values()))
        spread_after = 20 * math.log10(max(after.values()) / min(after.values()))
        print(f"\nразброс банка: {spread_before:.1f} дБ -> {spread_after:.1f} дБ")

    return trims


def write_module(trims: dict, path: str = OUTPUT_PATH) -> str:
    lines = [
        '"""',
        "bank_calibration.py — GENERATED. Do not edit by hand.",
        "",
        "Per-patch loudness trims in Total Level steps (0.75 dB each), added",
        "to a patch's carriers when it is selected. Positive attenuates,",
        "negative boosts by removing attenuation.",
        "",
        "Regenerate with:  python3 python/calibrate_bank.py --write",
        '"""',
        "",
        "TRIMS = {",
    ]
    for name in sorted(trims):
        lines.append(f"    {name!r}: {trims[name]},")
    lines.append("}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def main(argv):
    trims = calibrate(verbose=True)
    if "--write" in argv:
        path = write_module(trims)
        print(f"\nwrote {os.path.relpath(path, os.path.dirname(_HERE))}")
    else:
        print("\n(--write чтобы записать в bank_calibration.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
