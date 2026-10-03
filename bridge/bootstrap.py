"""
bridge/bootstrap.py — one command between "unzipped" and "making music".

    python3 bridge/bootstrap.py

Run it and it works out where it is, builds the C chip cores if there is a
compiler, falls back to the pure-Python cores if there is not, renders a
short test pattern to prove the whole path works, and prints what it
found. It changes nothing outside the chipgen directory and needs no
network.

The audience is as much a language model as a person. A model that has
just been handed chipgen.zip has no idea whether this sandbox has gcc, or
numpy, or how loud a working render is supposed to be — so bootstrap
answers all of that in one pass, and `--json` gives the same answers in a
form worth parsing.

    python3 bridge/bootstrap.py --json      machine-readable report
    python3 bridge/bootstrap.py --quick     skip the render self-test
    python3 bridge/bootstrap.py --manifest  refresh bridge/manifest.json
"""

import json
import os
import platform
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
PYTHON_DIR = os.path.join(ROOT, "python")
OUTPUT_DIR = os.path.join(ROOT, "output")

if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

#: Short enough to render in well under a second on the slowest path
#: (pure-Python cores, no numpy), long enough to exercise FM, PSG tone,
#: PSG noise and the DAC — if this renders, everything renders.
SELF_TEST = """\
bpm 150
lpb 4
inst fm0 bass
inst fm1 square_lead
cols fm0 fm1 psg0 noise dac

D-2  ...  D-5   ...  kick
...  A-4  A-5:6 w1   hat
"""


def _probe_runtime() -> dict:
    import audio
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "numpy": audio.HAVE_NUMPY,
        "scipy": audio.HAVE_SCIPY,
        "dsp_backend": audio.backend_name(),
    }


def _probe_cores() -> dict:
    import build_cores
    import core_loader

    compiler = build_cores.find_compiler()
    built = build_cores.ensure_cores(quiet=True)
    status = core_loader.status()
    return {
        "compiler": compiler,
        "libraries": {name: os.path.relpath(path, ROOT)
                      for name, path in sorted(built.items())},
        "ym2612": status["ym2612"],
        "sn76489": status["sn76489"],
        "notes": status["notes"],
    }


def _self_test() -> dict:
    import audio
    import chipgen

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    wav = os.path.join(OUTPUT_DIR, "bootstrap_check.wav")
    vgm = os.path.join(OUTPUT_DIR, "bootstrap_check.vgm")

    started = time.time()
    result = chipgen.compose(SELF_TEST, wav=wav, vgm=vgm,
                             title="chipgen bootstrap check")
    elapsed = time.time() - started

    peak = audio.peak(result.audio)
    return {
        "ok": bool(len(result.audio)) and peak > 0.01,
        "events": len(result.events),
        "duration": round(result.duration, 3),
        "peak": round(peak, 4),
        "rms": round(audio.rms(result.audio), 4),
        "cpu_seconds": round(elapsed, 2),
        "realtime_factor": round(elapsed / max(result.duration, 1e-6), 2),
        "wav": os.path.relpath(wav, ROOT),
        "vgm": os.path.relpath(vgm, ROOT),
        "warnings": result.warnings,
    }


def write_manifest(path: str = None) -> str:
    """Dump chipgen.info() to bridge/manifest.json."""
    import chipgen
    path = path or os.path.join(HERE, "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(chipgen.info(), fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def report(quick: bool = False) -> dict:
    data = {"root": ROOT, "runtime": _probe_runtime(), "cores": _probe_cores()}
    data["self_test"] = None if quick else _self_test()
    data["ready"] = quick or bool(data["self_test"]["ok"])
    return data


def _print_human(data: dict):
    runtime, cores, test = data["runtime"], data["cores"], data["self_test"]

    print()
    print("  chipgen bridge — " + ("READY" if data["ready"] else "NOT READY"))
    print("  " + "-" * 58)
    print(f"  python      {runtime['python']} "
          f"({runtime['implementation']}, {runtime['platform']}/{runtime['machine']})")
    print(f"  dsp         {runtime['dsp_backend']}"
          f"{'' if runtime['numpy'] else '   (numpy not installed — that is fine)'}")

    if cores["ym2612"] == "native":
        print(f"  chip cores  native C, built with {cores['compiler']}")
    else:
        print("  chip cores  PURE PYTHON fallback"
              + (f" (no compiler found)" if not cores["compiler"] else ""))
        print("              the YM2612 fallback approximates the chip; install")
        print("              a C compiler and rerun for the cycle-accurate core")
    for note in cores["notes"]:
        print(f"              note: {note}")

    if test:
        verdict = "OK" if test["ok"] else "FAILED"
        print(f"  self-test   {verdict} — {test['events']} events, "
              f"{test['duration']}s, peak {test['peak']}, "
              f"{test['realtime_factor']}x realtime")
        print(f"  wrote       {test['wav']}")
        print(f"              {test['vgm']}")
        for warning in test["warnings"]:
            print(f"  warning     {warning}")

    print()
    print("  Compose:")
    print("    python3 python/chipgen.py song.trk -o song.wav --vgm song.vgm")
    print("    python3 -c \"import sys; sys.path.insert(0,'python'); import chipgen;\\")
    print("               chipgen.compose(open('song.trk').read(), wav='song.wav')\"")
    print()
    print("  Read next:")
    print("    START_HERE.md          what to write, in one page")
    print("    bridge/manifest.json   every event, instrument and range, as JSON")
    print("    python3 python/chipgen.py --info    the same, freshly generated")
    print()


def main(argv):
    quick = "--quick" in argv
    as_json = "--json" in argv

    if "--manifest" in argv:
        path = write_manifest()
        if not as_json:
            print(f"wrote {os.path.relpath(path, ROOT)}")

    data = report(quick=quick)
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        _print_human(data)
    return 0 if data["ready"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
