"""How well the phrase anchors (python/agentic/anchors.py) find the places
where a module's own patterns begin — the one structure in real music
that comes with every file, and the nearest thing to marked phrases.

    python3 -I examples/agentic/references/anchors_eval.py MODULES_DIR \\
        [--seconds 90] [--out anchors_eval.json]

Each module under MODULES_DIR (any depth) is kept as a reference for the
first `seconds`, its anchors computed from its notes, and compared with
the bar lines where a pattern starts: precision (anchors on such a bar
line), recall (such bar lines found), the chance rate (their share of all
bar lines) and the precision of a bar line every four bars. A module
whose grid came from its audio, or whose every bar starts a pattern, says
nothing either way and is left out of the summary. The modules are other
people's music: they are read where they are, never copied.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "python"))

from agentic import anchors as A                  # noqa: E402
from agentic import fixtures, openmpt as O        # noqa: E402
from agentic import references as REF, state as S  # noqa: E402

MODULES = (".it", ".xm", ".s3m", ".mod")


def one(path: str, seconds: float) -> dict:
    tmp = tempfile.mkdtemp()
    started = time.time()
    try:
        p = S.Project.create(os.path.join(tmp, "p"), fixtures.masked_lead())
        r = REF.add(p, path, tags=["rhythm"], seconds=seconds, check=False)
        ref = REF.get(p, r["id"])
        starts = ref["grid"]["bars"]
        rec = A.of_reference(p, r["id"])
        with open(path, "rb") as handle:
            data = handle.read()
        with O.Module(data) as m:
            _s, played = m.render(44100, seconds)
        pattern_t = sorted({round(x["t"], 3) for x in played
                            if x["row"] == 0 and x["t"] > 0.5})

        def bar_line(t):
            k = min(range(len(starts)), key=lambda i: abs(starts[i] - t))
            return k if abs(starts[k] - t) < 0.06 else None
        truth = {b for b in map(bar_line, pattern_t) if b}
        lines = max(1, len(starts) - 1)
        anchors = rec["anchors"]
        clear = [a for a in anchors if a["certainty"] == "clear"]
        every4 = [k for k in range(1, lines) if k % 4 == 0]
        return {"file": os.path.basename(path), "grid": ref["grid"]["source"],
                "bar_lines": lines, "pattern_lines": len(truth),
                "anchors": len(anchors), "clear": len(clear),
                "hits": sum(1 for a in anchors if a["bar"] in truth),
                "clear_hits": sum(1 for a in clear if a["bar"] in truth),
                "found": len({a["bar"] for a in anchors} & truth),
                "every4": len(every4),
                "every4_hits": sum(1 for k in every4 if k in truth),
                "seconds": round(time.time() - started, 1)}
    except Exception as error:
        return {"file": os.path.basename(path),
                "error": f"{type(error).__name__}: {str(error)[:120]}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def summary(rows) -> dict:
    ok = [r for r in rows if r.get("grid") == "score" and
          r["pattern_lines"] and r["pattern_lines"] < 0.9 * r["bar_lines"]]

    def share(a, b):
        return round(a / b, 3) if b else None
    return {"modules": len(rows), "informative": len(ok),
            "clear_precision": share(sum(r["clear_hits"] for r in ok),
                                     sum(r["clear"] for r in ok)),
            "precision": share(sum(r["hits"] for r in ok),
                               sum(r["anchors"] for r in ok)),
            "recall": share(sum(r["found"] for r in ok),
                            sum(r["pattern_lines"] for r in ok)),
            "chance": share(sum(r["pattern_lines"] for r in ok),
                            sum(r["bar_lines"] for r in ok)),
            "every4_precision": share(sum(r["every4_hits"] for r in ok),
                                      sum(r["every4"] for r in ok))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("modules")
    ap.add_argument("--seconds", type=float, default=90.0)
    ap.add_argument("--out", default="anchors_eval.json")
    a = ap.parse_args(argv)
    if not O.available():
        print("libopenmpt is not installed: modules cannot be played")
        return 1
    files = sorted(os.path.join(d, f) for d, _s, fs in os.walk(a.modules)
                   for f in fs if f.lower().endswith(MODULES))
    rows = []
    for f in files:
        rows.append(one(f, a.seconds))
        print(json.dumps(rows[-1]), flush=True)
    out = {"summary": summary(rows), "modules": rows}
    with open(a.out, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=1)
    print(json.dumps(out["summary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
