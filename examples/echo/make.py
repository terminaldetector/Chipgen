"""An echo written into an instrument's envelopes: build it, play it, measure
whether it does what it is for, and write the A/B files.

The technique is studied from Skaven's "Fourth Symmetriad" (instrument 10,
"Sine.softecho", as read from the module's header in the reference corpus
audit): one sample, and a volume envelope that strikes three times — the
note and two quieter repeats — while the pan envelope throws the repeats
right and then left and the filter envelope closes each one a little more.
With NNA "note off" the repeats of one note keep sounding under the next.
No new sample is needed for the echo, and the pattern does not hold it: the
notes stay the caller's.

The numbers here are this project's own (repeats 12 ticks apart, its own
levels, pan and filter) on a saw, and the melody is its own. A and B differ
in one thing only, the volume envelope's repeats (`echo_on_*` against
`echo_off_*`); `echo_nna_cut_motif` changes only the NNA.

    python3 examples/echo/make.py [OUT_DIR]

writes the modules, MP3s of each A/B pair and of the etude in context, and
`report.json` with the readings. The etude, the finalist, is also played by
Schism Tracker itself when it is installed. Nothing here says the echo
sounds good; the readings say it is there, where it should be, and that
nothing else moved.
"""

import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from schism import it_write, notation, render as R, schismtracker  # noqa: E402
from schism.forge import phases, program  # noqa: E402

RATE = 44100
TEMPO = 125
TICK_S = 2.5 / TEMPO
HIT_TICKS = (0, 12, 24)
C5 = 523.2511


def build(name):
    module, report = notation.compile_file(os.path.join(HERE, name))
    return module, it_write.build(module)


def play(blob, engine="openmpt", seconds=None):
    frames = R.render_frames(blob, RATE, seconds, engine)
    return list(frames[0::2]), list(frames[1::2])


def _env(x):
    return phases.envelope(phases.centred(x, RATE), RATE)


def _db(v):
    return 20.0 * math.log10(v) if v > 1e-9 else -180.0


def hits(left, right, start_s=0.0, f0=C5):
    """The note and its repeats: level, side and brightness of each, and
    how far each repeat rises above the trough before it."""
    el, er = _env(left), _env(right)
    mono = [(a + b) * 0.5 for a, b in zip(left, right)]
    em = _env(mono)
    out = []
    for k, tick in enumerate(HIT_TICKS):
        t = start_s + tick * TICK_S
        i = int(round(t * 1000.0))
        win = range(max(0, i - 5), min(len(em), i + 40))
        if not win:
            break
        peak_i = max(win, key=lambda j: em[j])
        row = {"expected_ms": round(t * 1000.0, 1),
               "peak_ms": float(peak_i),
               "level_db": round(_db(em[peak_i]), 2),
               "balance_db": round(_db(max(er[peak_i], 1e-9))
                                   - _db(max(el[peak_i], 1e-9)), 2)}
        if k:
            before = em[max(0, i - 30):max(1, i - 2)]
            trough = min(before) if before else em[peak_i]
            row["rise_db"] = round(_db(em[peak_i]) - _db(max(trough, 1e-9)),
                                   2)
        seg = mono[int(t * RATE):int((t + 0.06) * RATE)]
        bright, _ = phases._brightness(seg, RATE, f0)
        row["brightness"] = None if bright is None else round(bright[0], 3)
        out.append(row)
    return out


def check(on, off):
    """The hypothesis, point by point."""
    rises = [h.get("rise_db") for h in on[1:]]
    rises_off = [h.get("rise_db") for h in off[1:]]
    sides = [h["balance_db"] for h in on[1:]]
    bright = [h["brightness"] for h in on]
    return {
        "repeats_rise_6db": all(r is not None and r >= 6.0 for r in rises),
        "dry_has_no_repeats": all(r is None or r < 1.0 for r in rises_off),
        "first_repeat_right": sides[0] >= 3.0,
        "second_repeat_left": sides[1] <= -3.0,
        "repeats_darker": all(b is not None and bright[0] is not None
                              and b < bright[0] for b in bright[1:]),
        "first_hit_unchanged_db": round(on[0]["level_db"]
                                        - off[0]["level_db"], 3),
    }


def mp3(left, right, path):
    from array import array
    frames = array("f")
    for a, b in zip(left, right):
        frames.append(a)
        frames.append(b)
    wav = path[:-4] + ".wav"
    from schism import openmpt
    openmpt.write_wav(frames, wav, RATE)
    R.to_mp3(wav, path)
    os.remove(wav)
    return path


def ab(a_left, a_right, b_left, b_right, path, gap=0.6):
    """A, a pause, B in one file."""
    pause = [0.0] * int(gap * RATE)
    return mp3(a_left + pause + b_left, a_right + pause + b_right, path)


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    renders = 0
    report = {"technique": "echo written into the envelopes",
              "source": {"module": "Skaven - Fourth Symmetriad "
                                   "(skaven-fourth_symmetriad.it)",
                         "instrument": "10 Sine.softecho",
                         "read_from": "the reference corpus audit "
                                      "(corpus_facts.json): the module's "
                                      "instrument header, not a render",
                         "observed": {
                             "volume": "64@0 13@5 0@15 30@16 7@21 0@31 "
                                       "15@32 5@34 0@44 (value@tick)",
                             "pan": "0@0 0@15 13@16 13@31 -13@32 -12@59",
                             "filter": "32@0 4@15 24@16 3@31 16@32 2@45",
                             "nna": "note off"}},
              "tempo": TEMPO, "tick_ms": TICK_S * 1000.0}
    # -- one note: the hits --------------------------------------------------
    on_mod, on_blob = build("echo_on_note.sch")
    off_mod, off_blob = build("echo_off_note.sch")
    report["instrument"] = program.explain_instrument(on_mod, 1)
    lo, ro = play(on_blob, seconds=1.4)
    lf, rf = play(off_blob, seconds=1.4)
    renders += 2
    report["note"] = {"echo": hits(lo, ro), "dry": hits(lf, rf)}
    report["hypothesis"] = {
        "problem": "a line wants an echo without a delay effect or a second "
                   "channel",
        "parameter": "the volume envelope's repeats (venv), with the pan "
                     "and filter envelopes and NNA note-off as they are",
        "expected": "two repeats 12 ticks apart, each at least 6 dB above "
                    "the trough before it, the first on the right, the "
                    "second on the left, each darker; the first hit the "
                    "same as without them",
        "result": check(report["note"]["echo"], report["note"]["dry"])}
    note_on = phases.note([(a + b) * 0.5 for a, b in zip(lo, ro)], RATE, C5,
                          6 * TICK_S)
    note_off = phases.note([(a + b) * 0.5 for a, b in zip(lf, rf)], RATE, C5,
                           6 * TICK_S)
    report["note"]["phases"] = {"echo": note_on["phases"]["attack"],
                                "dry": note_off["phases"]["attack"]}
    ab(lo, ro, lf, rf, os.path.join(out_dir, "ab_note_echo_then_dry.mp3"))
    # -- the motif: A/B, and the NNA alone -------------------------------
    motif = {}
    for name in ("echo_on_motif", "echo_off_motif", "echo_nna_cut_motif"):
        module, blob = build(f"{name}.sch")
        left, right = play(blob, seconds=8.5)
        renders += 1
        motif[name] = (left, right, blob)
        mono = [(a + b) * 0.5 for a, b in zip(left, right)]
        motif[name + "_rms"] = round(_db(math.sqrt(
            sum(v * v for v in mono) / len(mono))), 2)
    # what is left of the previous note when the next one starts: in the
    # fast part (a note every 4 rows, 24 ticks) the previous note's second
    # repeat, thrown left, lands on the next note-on. A centred note reads
    # 0 dB right-minus-left; the repeat under it pulls the reading left.
    row_s = 6 * TICK_S
    def under(left, right):
        out = []
        for r in (36, 40, 44, 48):
            a = int((r * row_s + 0.005) * RATE)
            b = int((r * row_s + 0.06) * RATE)
            def rms(x):
                return math.sqrt(sum(v * v for v in x) / len(x))
            out.append(round(_db(rms(right[a:b])) - _db(rms(left[a:b])), 2))
        return out
    report["motif"] = {
        "rms_db": {k[:-4]: v for k, v in motif.items() if k.endswith("_rms")},
        "balance_at_next_note_db": {
            "nna_off": under(*motif["echo_on_motif"][:2]),
            "nna_cut": under(*motif["echo_nna_cut_motif"][:2])}}
    ab(*motif["echo_on_motif"][:2], *motif["echo_off_motif"][:2],
       os.path.join(out_dir, "ab_motif_echo_then_dry.mp3"))
    ab(*motif["echo_on_motif"][:2], *motif["echo_nna_cut_motif"][:2],
       os.path.join(out_dir, "ab_motif_nna_off_then_cut.mp3"))
    for name in ("echo_on_motif", "echo_off_motif", "echo_nna_cut_motif"):
        with open(os.path.join(out_dir, f"{name}.it"), "wb") as handle:
            handle.write(motif[name][2])
    # -- the etude in context: the finalist, in both players ---------------------
    report["etude"] = {}
    for name in ("etude_echo", "etude_dry"):
        module, blob = build(f"{name}.sch")
        fit = R.fit_peak(module, 0.89)
        blob = it_write.build(module)
        with open(os.path.join(out_dir, f"{name}.it"), "wb") as handle:
            handle.write(blob)
        left, right = play(blob)
        renders += 1
        mp3(left, right, os.path.join(out_dir, f"{name}.mp3"))
        peak = max(max(abs(v) for v in left), max(abs(v) for v in right))
        report["etude"][name] = {"seconds": round(len(left) / RATE, 2),
                                 "peak_openmpt": round(peak, 3),
                                 "mix_volume": module.mix_volume,
                                 "fit": fit.text() if fit else None}
    if schismtracker.available():
        _, blob = build("etude_echo.sch")
        module, _ = notation.compile_file(os.path.join(HERE, "etude_echo.sch"))
        R.fit_peak(module, 0.89)
        blob = it_write.build(module)
        left, right = play(blob, engine="schism")
        renders += 1
        mp3(left, right, os.path.join(out_dir, "etude_echo.schism.mp3"))
        # and the note's hits as Schism plays them
        ls, rs = play(on_blob, engine="schism")
        renders += 1
        theirs = hits(ls[:int(1.4 * RATE)], rs[:int(1.4 * RATE)])
        report["schism"] = {
            "version": schismtracker.version(),
            "note_hits": theirs,
            "level_difference_db": [round(a["level_db"] - b["level_db"], 2)
                                    for a, b in zip(report["note"]["echo"],
                                                    theirs)],
            "balance_difference_db": [round(a["balance_db"]
                                            - b["balance_db"], 2)
                                      for a, b in zip(report["note"]["echo"],
                                                      theirs)],
            "etude_peak": round(max(max(abs(v) for v in left),
                                    max(abs(v) for v in right)), 3)}
    else:
        report["schism"] = None
    report["costs"] = {"renders": renders,
                       "seconds": round(time.time() - started, 1),
                       "model_calls": 0, "model_tokens": None}
    report["listening"] = ("not done here: the readings show the repeats are "
                           "there and where; whether the line sounds better "
                           "with them is for the A/B files")
    with open(os.path.join(out_dir, "report.json"), "w") as handle:
        json.dump(report, handle, indent=1)
    return report


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")
    result = main(out)
    print(json.dumps({"hypothesis": result["hypothesis"]["result"],
                      "note": result["note"]["echo"],
                      "schism": result["schism"] and {
                          k: result["schism"][k] for k in
                          ("level_difference_db", "balance_difference_db")},
                      "costs": result["costs"]}, indent=1))
