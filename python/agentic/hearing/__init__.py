"""hearing — Essentia as the agent's hearing plugin.

The agent renders with Chipgen, calls these tools on the audio, reads
structured observations, changes the score and renders again. This
package is the Chipgen side, pure standard library like the rest of the
engine: it runs the plugin in its own interpreter (numpy, Essentia
built from the fork) through worker.py, with a timeout, and keeps every result in a cache keyed by the
audio's hash, the function and its arguments — an edit that changes one
voice re-analyses that voice's file only.

    the interpreter   CHIPGEN_HEARING_PYTHON, else /opt/hearing-venv/bin/
                      python if it exists
    Essentia          its bindings at CHIPGEN_ESSENTIA_PATH (default
                      /opt/essentia/python)
    the cache         CHIPGEN_HEARING_CACHE, else ~/.cache/chipgen-hearing

Without the interpreter a call returns status "unavailable" and why;
nothing is simulated.
"""

import hashlib
import json
import os
import subprocess
import sys
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.join(HERE, "worker.py")
#: bump when an analysis changes what it returns: old cache entries miss
SCHEMA = 2
_STATUS = None
_SHA = {}


def interpreter() -> Optional[str]:
    py = os.environ.get("CHIPGEN_HEARING_PYTHON")
    if py:
        return py
    default = "/opt/hearing-venv/bin/python"
    return default if os.path.exists(default) else None


def cache_dir() -> str:
    d = os.environ.get("CHIPGEN_HEARING_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "chipgen-hearing")
    os.makedirs(d, exist_ok=True)
    return d


def file_sha256(path: str) -> str:
    st = os.stat(path)
    stamp = (path, st.st_size, st.st_mtime_ns)
    if stamp not in _SHA:
        h = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                h.update(block)
        _SHA[stamp] = h.hexdigest()
    return _SHA[stamp]


def _unavailable(function: str, why: str, tool: str = "hearing") -> dict:
    return {"tool": tool, "function": function, "status": "unavailable",
            "why": why, "observations": {}, "confidence": None,
            "limitations": ["nothing was analysed"]}


def _run(request: dict, timeout: float) -> dict:
    py = interpreter()
    if not py:
        return _unavailable(request["function"], "no hearing interpreter: "
                            "set CHIPGEN_HEARING_PYTHON (see bridge/"
                            "HEARING_PLUGINS.md)")
    env = dict(os.environ)
    try:
        done = subprocess.run([py, "-I", WORKER], input=json.dumps(request),
                              capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {**_unavailable(request["function"], f"timed out after "
                               f"{timeout:.0f} s"), "status": "error"}
    except OSError as error:
        return _unavailable(request["function"], f"cannot run {py}: "
                                                 f"{error}")
    lines = [l for l in done.stdout.splitlines() if l.startswith("{")]
    if not lines:
        return {**_unavailable(request["function"], "the worker returned "
                               "nothing: " + done.stderr[-400:]),
                "status": "error"}
    return json.loads(lines[-1])


def status(refresh: bool = False) -> dict:
    """Whether the plugin runs here, and its version."""
    global _STATUS
    if _STATUS is None or refresh:
        r = _run({"function": "status"}, 120)
        _STATUS = {"interpreter": interpreter(),
                   **(r.get("observations") or {}),
                   **({"why": r.get("why")} if r.get("status") != "ok"
                      else {})}
    return _STATUS


def call(function: str, timeout: float = 600.0, cache: bool = True,
         **request) -> dict:
    """One plugin function on one file (and a reference, for the
    comparisons). -> the worker's envelope; from the cache when the same
    audio was asked the same question."""
    request = {k: v for k, v in request.items() if v is not None}
    request["function"] = function
    for key in ("file", "reference"):
        if key in request:
            request[key] = os.path.abspath(request[key])
            if not os.path.isfile(request[key]):
                return {**_unavailable(function, f"no such file: "
                                                 f"{request[key]}"),
                        "status": "error"}
    ident = {k: v for k, v in request.items()
             if k not in ("file", "reference")}
    ident["schema"] = SCHEMA
    if "file" in request:
        ident["file_sha256"] = file_sha256(request["file"])
    if "reference" in request:
        ident["reference_sha256"] = file_sha256(request["reference"])
    key = hashlib.sha256(json.dumps(ident, sort_keys=True).encode()
                         ).hexdigest()
    path = os.path.join(cache_dir(), f"{function}-{key[:24]}.json")
    if cache and os.path.exists(path):
        with open(path) as handle:
            out = json.load(handle)
        out["audio_file"] = request.get("file")
        out["cached"] = True
        return out
    out = _run(request, timeout)
    out["cached"] = False
    if cache and out.get("status") == "ok":
        with open(path, "w") as handle:
            json.dump(out, handle)
    return out


# -- the tools ---------------------------------------------------------------------------------
def inspect_audio(file, start=None, end=None, **kw):
    return call("inspect_audio", file=file, start=start, end=end, **kw)


def analyze_rhythm(file, start=None, end=None, beats_per_bar=4,
                   window_bars=4, bpm_hint=None, **kw):
    return call("analyze_rhythm", file=file, start=start, end=end,
                beats_per_bar=beats_per_bar, window_bars=window_bars,
                bpm_hint=bpm_hint, **kw)


def analyze_tonality(file, start=None, end=None, window_s=8.0, **kw):
    return call("analyze_tonality", file=file, start=start, end=end,
                window_s=window_s, **kw)


def analyze_timbre(file, start=None, end=None, **kw):
    return call("analyze_timbre", file=file, start=start, end=end, **kw)


def analyze_sections(file, start=None, end=None, boundaries=None,
                     min_section_s=6.0, **kw):
    return call("analyze_sections", file=file, start=start, end=end,
                boundaries=list(boundaries) if boundaries else None,
                min_section_s=min_section_s, **kw)


def compare_sections(file, reference, boundaries=None,
                     reference_boundaries=None, **kw):
    return call("compare_sections", file=file, reference=reference,
                boundaries=boundaries,
                reference_boundaries=reference_boundaries, **kw)


# -- before and after ------------------------------------------------------------------------
#: the scalar readings evaluate_revision compares: (analysis, path, basis)
METRICS = [
    ("rhythm", "measured.onsets_per_beat", "measured"),
    ("rhythm", "measured.ioi_in_beats.entropy_bits", "measured"),
    ("rhythm", "measured.ioi_in_beats.dominant_share", "measured"),
    ("rhythm", "measured.ioi_in_beats.distinct", "measured"),
    ("rhythm", "estimated.syncopation_share", "estimate"),
    ("rhythm", "measured.bars.distinct_patterns", "measured"),
    ("rhythm", "measured.bars.consecutive_bar_overlap", "measured"),
    ("rhythm", "measured.off_grid_ms.std", "measured"),
    ("rhythm", "estimated.tempo.bpm", "estimate"),
    ("timbre", "measured.loudness_lufs", "measured"),
    ("timbre", "measured.rms_db.mean", "measured"),
    ("timbre", "measured.centroid_hz.mean", "measured"),
    ("timbre", "measured.band_energy_share.6000-16000 Hz", "measured"),
    ("timbre", "measured.attack_ms.median", "measured"),
    ("timbre", "measured.dynamics.rms_db_p95_minus_p5", "measured"),
    ("tonality", "estimated.key.most_named", "estimate"),
    ("tonality", "estimated.predominant_pitch.range_semitones_p5_p95",
     "estimate"),
]
_ANALYSIS = {"rhythm": analyze_rhythm, "timbre": analyze_timbre,
             "tonality": analyze_tonality}


def read(result: dict, path: str):
    node = result.get("observations", {})
    for part in path.split("."):
        if isinstance(node, list) and part.isdigit() and \
                int(part) < len(node):
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def evaluate_revision(before: str, after: str, target: dict = None,
                      analyses=("rhythm", "timbre", "tonality"),
                      start=None, end=None, bpm_hint=None) -> dict:
    """The same analyses on two renders and every reading's change. A
    `target` ({"analysis", "path", "direction": "up"|"down", "by": x} or
    "at_least"/"at_most") is checked and reported as met or not; nothing
    else is called better or worse."""
    res = {}
    for name in analyses:
        fn = _ANALYSIS[name]
        kw = {"bpm_hint": bpm_hint} if name == "rhythm" and bpm_hint \
            else {}
        res[name] = (fn(before, start=start, end=end, **kw),
                     fn(after, start=start, end=end, **kw))
    deltas = []
    for name, path, basis in METRICS:
        if name not in res:
            continue
        a, b = (read(r, path) for r in res[name])
        if a is None and b is None:
            continue
        row = {"analysis": name, "reading": path, "basis": basis,
               "before": a, "after": b}
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            row["change"] = round(b - a, 3)
        elif a != b:
            row["change"] = "changed"
        deltas.append(row)
    out = {"tool": "hearing", "function": "evaluate_revision",
           "before_file": before, "after_file": after,
           "observations": {"deltas": deltas},
           "status": "ok" if all(r[0].get("status") == "ok" and
                                 r[1].get("status") == "ok"
                                 for r in res.values()) else "error",
           "confidence": None,
           "limitations": ["changes are measured differences between two "
                           "renders; whether they are improvements is the "
                           "target's question, and the target is the "
                           "agent's choice"]}
    if out["status"] != "ok":
        out["why"] = "; ".join(f"{n}: {r[i].get('why')}"
                               for n, r in res.items() for i in (0, 1)
                               if r[i].get("status") != "ok")
    if target:
        out["target"] = check_target(target, res)
    return out


def check_target(target: dict, res: dict) -> dict:
    a, b = (read(r, target["path"]) for r in res[target["analysis"]])
    met, why = None, None
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        why = "the reading is missing before or after"
    else:
        met = True
        if target.get("direction") == "up":
            met = b - a >= float(target.get("by", 0.0)) and b > a
        elif target.get("direction") == "down":
            met = a - b >= float(target.get("by", 0.0)) and b < a
        if "at_least" in target:
            met = met and b >= float(target["at_least"])
        if "at_most" in target:
            met = met and b <= float(target["at_most"])
    return {**target, "before": a, "after": b, "met": met,
            **({"why": why} if why else {})}
