"""compare_engines.py — libopenmpt and Schism Tracker, side by side.

    python3 tools/compare_engines.py [--interpolation MODE] [demos/first_light.sch ...]

Renders each score through both players and prints the length, the peak,
the RMS, the left/right balance and the energy in each octave band, as
Schism's number minus libopenmpt's. The table in docs/COVERAGE.md came
from this script. Needs numpy for the spectra (pip install numpy) and both
players installed.
"""

import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from schism import it_write, notation, openmpt, schismtracker  # noqa: E402

EDGES = [20, 40, 80, 160, 320, 640, 1280, 2560, 5120, 10240, 20480]


def _bands(np, mono):
    n = 8192
    window = np.hanning(n)
    total = None
    count = 0
    for start in range(0, len(mono) - n, n):
        power = np.abs(np.fft.rfft(mono[start:start + n] * window)) ** 2
        total = power if total is None else total + power
        count += 1
    total /= max(count, 1)
    freqs = np.fft.rfftfreq(n, 1 / 44100)
    return np.array([10 * np.log10(total[(freqs >= lo) & (freqs < hi)].sum()
                                   + 1e-20)
                     for lo, hi in zip(EDGES, EDGES[1:])])


def main(argv):
    mode = "spline"
    if "--interpolation" in argv:
        at = argv.index("--interpolation")
        mode = argv[at + 1]
        argv = argv[:at] + argv[at + 2:]
    try:
        import numpy as np
    except ImportError:
        print("this report needs numpy: pip install numpy", file=sys.stderr)
        return 2
    if not (openmpt.available() and schismtracker.available()):
        print("this report needs both libopenmpt and Schism Tracker "
              "(apt install libopenmpt0 schism)", file=sys.stderr)
        return 2
    paths = argv or sorted(glob.glob(os.path.join(ROOT, "demos", "*.sch")))
    print(f"libopenmpt {openmpt.version()}   {schismtracker.version()}   "
          f"(Schism interpolation: {mode})")
    print("octave bands " + " ".join(f"{lo:>6d}" for lo in EDGES[:-1]) + " Hz")
    for path in paths:
        module, report = notation.compile_file(path)
        blob = it_write.build(module)
        with openmpt.Song(blob) as song:
            a = np.array(song.render(44100), dtype=np.float64).reshape(-1, 2)
        b = np.array(schismtracker.render(blob, 44100, mode),
                     dtype=np.float64).reshape(-1, 2)
        n = min(len(a), len(b))
        a, b = a[:n], b[:n]
        name = os.path.splitext(os.path.basename(path))[0]
        print(f"\n{name}: score {report.seconds:.2f} s, libopenmpt "
              f"{len(a) / 44100:.2f} s, Schism {len(b) / 44100:.2f} s")
        for label, x in (("libopenmpt", a), ("Schism", b)):
            left = np.sqrt((x[:, 0] ** 2).mean())
            right = np.sqrt((x[:, 1] ** 2).mean())
            print(f"  {label:10s} peak {np.abs(x).max():.3f}  "
                  f"rms {np.sqrt((x ** 2).mean()):.4f}  "
                  f"L/R {20 * np.log10(left / right):+.2f} dB")
        delta = _bands(np, b.mean(axis=1)) - _bands(np, a.mean(axis=1))
        print("  Schism - libopenmpt (dB)  "
              + " ".join(f"{v:+6.1f}" for v in delta))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
