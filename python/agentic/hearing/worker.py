"""worker.py — the hearing plugin's process: one JSON request on stdin,
one JSON result on stdout. Run by agentic/hearing/__init__.py in the
hearing interpreter (numpy, Essentia built from the fork) with -I, so
nothing is imported from the
working directory; this folder and Essentia's bindings are put on the
path explicitly.

Every result is the envelope the agent reads:

    {"tool", "function", "audio_file", "audio_sha256", "start_seconds",
     "end_seconds", "observations", "confidence", "limitations",
     "model_or_algorithm", "status", "elapsed_s"}

`observations` keeps measured values, estimates and candidates apart; `confidence` is null unless an algorithm gives one with
a documented scale, and then it sits inside the observations next to
that scale. `status` is "ok", "unavailable" (with "why") or "error".
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.append(os.environ.get("CHIPGEN_ESSENTIA_PATH",
                               "/opt/essentia/python"))


def _envelope(tool, function, req, **kw):
    out = {"tool": tool, "function": function,
           "audio_file": req.get("file"), "audio_sha256": None,
           "start_seconds": req.get("start"),
           "end_seconds": req.get("end"), "observations": {},
           "confidence": None, "limitations": [], "model_or_algorithm": "",
           "status": "ok"}
    out.update(kw)
    return out


def _essentia(function, req):
    import numpy as np
    import audio as A
    import essentia_ear as E
    path = req["file"]
    start, end = req.get("start"), req.get("end")
    x = A.load(path, E.RATE, 1, start, end)
    env = _envelope("essentia", function, req, audio_sha256=A.sha256(path),
                    model_or_algorithm=E.version())
    if end is None:
        env["end_seconds"] = round((start or 0) + len(x) / E.RATE, 3)
    env["start_seconds"] = start or 0.0
    if function == "inspect_audio":
        st = A.load(path, E.RATE, 2, start, end)
        peak = float(np.abs(st).max()) if len(st) else 0.0
        rms = float(np.sqrt(np.mean(st ** 2))) if len(st) else 0.0
        obs = {"measured": {"seconds": round(len(x) / E.RATE, 3),
                            "channels_decoded": 2, "rate": E.RATE,
                            "peak_dbfs": round(E._db(peak), 2),
                            "rms_dbfs": round(E._db(rms), 2),
                            "left_right_correlation": round(float(
                                np.corrcoef(st[:, 0], st[:, 1])[0, 1]), 3)
                            if len(st) > 1 and st[:, 0].std() > 0 and
                            st[:, 1].std() > 0 else None}}
        tim = E.timbre(x, st)
        obs["measured"]["loudness_lufs"] = tim["measured"].get(
            "loudness_lufs")
        env["observations"] = obs
        return env
    if function == "analyze_rhythm":
        res = E.rhythm(x, int(req.get("beats_per_bar", 4)),
                       int(req.get("window_bars", 4)),
                       req.get("bpm_hint"))
    elif function == "analyze_tonality":
        res = E.tonality(x, float(req.get("window_s", 8.0)))
    elif function == "analyze_timbre":
        st = A.load(path, E.RATE, 2, start, end)
        res = E.timbre(x, st)
    elif function == "analyze_sections":
        b = req.get("boundaries")
        if b and start:
            b = [t - start for t in b]
        res = E.sections(x, b, float(req.get("block_s", 1.0)),
                         float(req.get("min_section_s", 6.0)))
        _shift(res, start or 0.0)
    elif function == "compare_sections":
        ref = req["reference"]
        y = A.load(ref, E.RATE, 1, req.get("reference_start"),
                   req.get("reference_end"))
        res = E.compare_sections(x, y, req.get("boundaries"),
                                 req.get("reference_boundaries"))
        env["reference_file"] = ref
        env["reference_sha256"] = A.sha256(ref)
    else:
        raise ValueError(f"no Essentia function {function!r}")
    env["limitations"] = res.pop("limitations", [])
    env["observations"] = res
    return env


def _shift(res, start):
    if not start:
        return
    for s in res.get("measured", {}).get("sections", []):
        s["t0"] = round(s["t0"] + start, 2)
        s["t1"] = round(s["t1"] + start, 2)
    for c in res.get("candidates", []):
        if "t0" in c:
            c["t0"] = round(c["t0"] + start, 2)


ESSENTIA = {"inspect_audio", "analyze_rhythm", "analyze_tonality",
            "analyze_timbre", "analyze_sections", "compare_sections"}
def handle(req: dict) -> dict:
    function = req.get("function")
    started = time.time()
    tool = "essentia"
    try:
        if function == "status":
            out = {"status": "ok", "function": "status", "tool": "hearing",
                   "observations": status()}
        elif function in ESSENTIA:
            out = _essentia(function, req)
        else:
            raise ValueError(f"unknown function {function!r}")
    except ImportError as error:
        out = _envelope(tool, function, req, status="unavailable",
                        why=f"{type(error).__name__}: {error}")
    except Exception as error:                     # noqa: BLE001
        out = _envelope(tool, function, req, status="error",
                        why=f"{type(error).__name__}: {str(error)[:300]}")
    out["elapsed_s"] = round(time.time() - started, 2)
    return out


def status() -> dict:
    out = {"python": sys.version.split()[0], "essentia": None}
    try:
        import essentia_ear as E
        out["essentia"] = {"available": True, "version": E.version()}
    except Exception as error:                     # noqa: BLE001
        out["essentia"] = {"available": False,
                           "why": f"{type(error).__name__}: {error}"}
    return out


if __name__ == "__main__":
    request = json.loads(sys.stdin.read())
    print(json.dumps(handle(request)))
