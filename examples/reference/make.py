"""Six techniques of the reference corpus, each checked in the module it was
found in: one mechanism taken out of a copy (A against B), played where the
song plays it, alone and in the mix, in libopenmpt and Schism Tracker.

The addresses are the ones the corpus audit gave (Skaven, Manwe, FearofDark
and Necros; see docs/cards/). The modules are not in this repository:

    python3 examples/reference/make.py CORPUS_DIR OUT_DIR [CARD ...] [--cards=DIR]

where CORPUS_DIR holds `modules/<author>/<file>` as the reference corpus
zip unpacks; `--cards=DIR` also writes one Markdown card per technique and
`reference_ab.json` there (docs/cards/reference/ is that, for this
project's run). OUT_DIR gets `report.json` (every reading, every byte changed,
the hashes that show the originals untouched) and, per pair, an MP3 of A
then B alone and one of A then B in the mix. Nothing here says which side
sounds better; each card states what should change if the mechanism does
what the card says, and the report says whether it did.
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

from schism import abtest, corpus, openmpt, render as R  # noqa: E402
from schism.forge import phases  # noqa: E402

RATE = abtest.RATE


def _db(x):
    return 20.0 * math.log10(x) if x > 1e-9 else -180.0


def _mono(lr):
    return [(a + b) * 0.5 for a, b in zip(*lr)]


def _rms(x):
    return math.sqrt(sum(v * v for v in x) / len(x)) if x else 0.0


def _window(mono, start_s, stop_s):
    return mono[int(start_s * RATE):int(stop_s * RATE)]


def _centroid(mono, start_s, stop_s):
    seg = _window(mono, start_s, stop_s)
    if len(seg) < 256 or _rms(seg) < 1e-5:
        return None
    value, _ = phases._brightness(seg, RATE, None)
    return None if value is None else round(value[0], 1)


def _level(mono, start_s, stop_s):
    return round(_db(_rms(_window(mono, start_s, stop_s))), 2)


def _engines(rep):
    return list(rep["engines"])


def _audio(rep, engine):
    return rep["audio"][engine]["A"], rep["audio"][engine]["B"]


# -- the checks, one per card --------------------------------------------------
def _bands(mono, t):
    """dB in three bands of a 93 ms window at t, and the strongest region
    above 300 Hz (where a resonant filter puts its peak)."""
    from schism.forge import dsp
    seg = _window(mono, t, t + 4096 / RATE)
    if len(seg) < 4096:
        return None
    mags, binw = dsp.spectrum(seg, 0, 4096, RATE)

    def band(lo, hi):
        return round(10.0 * math.log10(sum(m * m for m in mags[
            int(lo / binw):int(hi / binw)]) + 1e-20), 1)
    smooth = [sum(mags[max(0, i - 3):i + 4]) for i in range(len(mags))]
    lo = int(300 / binw)
    k = max(range(lo, int(8000 / binw)), key=lambda i: smooth[i])
    return {"at_s": t, "low_60_300": band(60, 300),
            "mid_300_1200": band(300, 1200), "high_1200_5000": band(1200, 5000),
            "peak_hz": round(k * binw)}


def check_sweep(rep, card):
    """The filter envelope moves the instrument's resonant filter over the
    held note (instrument 1 is the same sample with no filter): in A the
    upper band (1.2-5 kHz) moves 10 dB or more along the note as the
    resonant peak comes down through it; in B (the envelope off) every band
    stays within 1 dB."""
    out = {}
    times = card["band_times_s"]
    for engine in _engines(rep):
        row = {}
        for label, lr in zip("AB", _audio(rep, engine)):
            mono = _mono(lr)
            track = [b for b in (_bands(mono, t) for t in times) if b]
            highs = [b["high_1200_5000"] for b in track]
            span = {key: round(max(b[key] for b in track)
                               - min(b[key] for b in track), 1)
                    for key in ("low_60_300", "mid_300_1200",
                                "high_1200_5000")}
            row[label] = {"bands": track, "span_db": span,
                          "high_rise_db": round(max(highs) - highs[0], 1),
                          "high_fall_db": round(max(highs) - highs[-1], 1)}
        row["holds"] = bool(row["A"]["span_db"]["high_1200_5000"] >= 10.0
                            and max(row["B"]["span_db"].values()) <= 1.0)
        out[engine] = row
    return out


def check_echo(rep, card):
    """The repeats are the envelope's: after the line stops, A goes on
    striking, right then left of the dry note; B (the envelope cut after its
    first strike) is silent, and the first strike is the same in both."""
    out = {}
    tail = card["tail_s"]
    for engine in _engines(rep):
        fa, fb = rep["engines"][engine]["A"], rep["engines"][engine]["B"]
        a, b = (_mono(x) for x in _audio(rep, engine))
        end = rep["address"]["seconds"]
        strikes_a = [s for s in fa["strikes"] if s["ms"] >= tail * 1000]
        strikes_b = [s for s in fb["strikes"] if s["ms"] >= tail * 1000]
        first_a = fa["strikes"][0] if fa["strikes"] else None
        dry = first_a["balance_db"] if first_a else None
        row = {"tail_from_s": tail,
               "tail_level_db": {"A": _level(a, tail, end),
                                 "B": _level(b, tail, end)},
               "tail_strikes": {"A": [{k: s[k] for k in ("ms", "db",
                                                         "balance_db")}
                                      for s in strikes_a],
                                "B": len(strikes_b)},
               "dry_balance_db": dry,
               "first_strike_db": {
                   "A": first_a["db"] if first_a else None,
                   "B": fb["strikes"][0]["db"] if fb["strikes"] else None}}
        sides = [s["balance_db"] - dry for s in strikes_a
                 if s["balance_db"] is not None and dry is not None]
        row["tail_sides_db"] = [round(v, 2) for v in sides]
        row["holds"] = bool(
            len(strikes_a) >= 2 and not strikes_b
            and row["tail_level_db"]["A"] - row["tail_level_db"]["B"] >= 15
            and any(v >= 2 for v in sides) and any(v <= -2 for v in sides)
            and row["first_strike_db"]["B"] is not None
            and abs(row["first_strike_db"]["A"]
                    - row["first_strike_db"]["B"]) <= 0.2)
        out[engine] = row
    return out


def _note_times(rep, card):
    """When the song plays each of the card's note rows, against the start
    of the window."""
    rows = rep["_rows"]
    start = rep["address"]["at"]
    order = rep["address"]["order"]
    return [round(corpus.first_time(rows, order, r) - start, 4)
            for r in card["note_rows"]]


def _brightness_db(mono, t):
    """1-6 kHz against 60-400 Hz, dB, on a 23 ms window at t: how far the
    filter is open, on a scale a sweep of a resonant filter moves by tens of
    dB."""
    from schism.forge import dsp
    seg = _window(mono, t, t + 1024 / RATE)
    if len(seg) < 1024 or _rms(seg) < 1e-5:
        return None
    mags, binw = dsp.spectrum(seg, 0, 1024, RATE)

    def band(lo, hi):
        return 10.0 * math.log10(sum(m * m for m in mags[
            int(lo / binw):int(hi / binw)]) + 1e-20)
    return round(band(1000, 6000) - band(60, 400), 1)


def check_carry(rep, card):
    """With carry the filter envelope is not started again by a note that
    follows a held note of the same instrument: that note stays as the
    last one left the filter (no sweep), while a note after a key-off
    starts it again (the sweep: about 30 dB from dark to open by 200 ms).
    With carry off (B) every note sweeps, and the notes after a key-off
    are the same in A and B."""
    out = {}
    times = _note_times(rep, card)
    for engine in _engines(rep):
        row = {"notes": [{"row": r, "after": a, "at_s": t}
                         for r, a, t in zip(card["note_rows"],
                                            card["after"], times)]}
        for label, lr in zip("AB", _audio(rep, engine)):
            mono = _mono(lr)
            sweeps = []
            for t in times:
                onset = [v for v in (_brightness_db(mono, t + d)
                                     for d in (0.0, 0.03)) if v is not None]
                body = [v for v in (_brightness_db(mono, t + d / 100.0)
                                    for d in range(12, 25, 3))
                        if v is not None]
                sweeps.append(round(max(body) - sum(onset) / len(onset), 1)
                              if onset and body else None)
            row[label] = {"sweep_db": sweeps}
        held = [i for i, a in enumerate(card["after"]) if a == "note"]
        released = [i for i, a in enumerate(card["after"]) if a != "note"]
        a, b = row["A"]["sweep_db"], row["B"]["sweep_db"]
        row["holds"] = bool(
            all(a[i] is not None and a[i] <= 8 for i in held)
            and all(b[i] is not None and b[i] >= 15 for i in held)
            and all(a[i] is not None and b[i] is not None
                    and abs(a[i] - b[i]) <= 0.5 and a[i] >= 15
                    for i in released))
        out[engine] = row
    return out


def check_trill(rep, card):
    """The looped pitch envelope throws the note up an octave and back
    every 6 ticks: A's pitch track spends a good part of the note an octave
    up; B (the envelope off) stays on the note."""
    out = {}
    for engine in _engines(rep):
        row = {}
        for label in "AB":
            pitch = rep["engines"][engine][label].get("pitch", {})
            track = [c for c in pitch.get("track_cents", []) if c is not None]
            up = sum(1 for c in track if c > 900)
            row[label] = {"voiced": len(track), "octave_up": up,
                          "share_up": round(up / len(track), 3)
                          if track else None}
        row["holds"] = bool(row["A"]["share_up"] is not None
                            and row["A"]["share_up"] >= 0.2
                            and row["B"]["share_up"] is not None
                            and row["B"]["share_up"] <= 0.05)
        out[engine] = row
    return out


def check_vibrato(rep, card):
    """K is H's vibrato carried on under a volume slide: over the K rows A's
    pitch wobbles; B (K -> D: the slides kept, the vibrato not) holds
    still. The H row itself is the same in both."""
    out = {}
    lo, hi = card["measure_s"]
    for engine in _engines(rep):
        row = {}
        for label in "AB":
            pitch = rep["engines"][engine][label].get("pitch", {})
            track = pitch.get("track_cents", [])
            part = [c for c in track[int(lo * 100):int(hi * 100)]
                    if c is not None]
            m = sum(part) / len(part) if part else None
            row[label] = {"voiced": len(part),
                          "spread_cents": round(math.sqrt(
                              sum((c - m) ** 2 for c in part) / len(part)), 1)
                          if part else None,
                          "range_cents": round(max(part) - min(part), 1)
                          if part else None}
        row["holds"] = bool(row["A"]["spread_cents"] is not None
                            and row["B"]["spread_cents"] is not None
                            and row["A"]["spread_cents"]
                            >= 3 * max(row["B"]["spread_cents"], 2.0))
        out[engine] = row
    return out


def check_slide(rep, card):
    """E0F pulls each note down while it sounds: in A the centroid (which
    follows the pitch for one sample) falls over the slide rows; in B (the
    E0F cleared) it stays."""
    out = {}
    for engine in _engines(rep):
        row = {}
        for label, lr in zip("AB", _audio(rep, engine)):
            mono = _mono(lr)
            falls = []
            for start, stop in card["slides_s"]:
                first = _centroid(mono, start, start + 0.046)
                last = _centroid(mono, stop - 0.046, stop)
                falls.append(round(last / first, 3) if first and last
                             else None)
            row[label] = {"last_over_first": falls}
        a = [r for r in row["A"]["last_over_first"] if r]
        b = [r for r in row["B"]["last_over_first"] if r]
        row["holds"] = bool(a and b and max(a) <= 0.85
                            and min(b) >= 0.95)
        out[engine] = row
    return out


def check_gate(rep, card):
    """D0F after each v40 pulls the held note down on the odd rows (64 to
    34 over a row of 3 ticks, about -2 dB on the row's average) and v40
    puts it back: in A the odd rows are quieter than the even ones by more
    than they are in B (the D0F cleared), where only the sample's own
    shape is left."""
    out = {}
    rows = rep["_rows"]
    start = rep["address"]["at"]
    order = rep["address"]["order"]
    edges = [corpus.first_time(rows, order, r) - start for r in range(17)]
    for engine in _engines(rep):
        row = {}
        for label, lr in zip("AB", _audio(rep, engine)):
            mono = _mono(lr)
            levels = [_level(mono, edges[r], edges[r + 1])
                      for r in range(1, 16)]
            odd = [v for r, v in zip(range(1, 16), levels) if r % 2]
            even = [v for r, v in zip(range(1, 16), levels) if not r % 2]
            row[label] = {"row_levels_db": levels,
                          "odd_minus_even_db": round(
                              sum(odd) / len(odd) - sum(even) / len(even),
                              2)}
        row["holds"] = bool(row["A"]["odd_minus_even_db"]
                            <= row["B"]["odd_minus_even_db"] - 1.5)
        out[engine] = row
    return out


def check_variants(rep, card):
    """Three instruments on one pitch: A's strikes differ in level or
    brightness by instrument; B (all instrument 21) strikes alike."""
    out = {}
    times = _note_times(rep, card)
    for engine in _engines(rep):
        row = {"strikes_at_s": times, "instruments_A": card["instruments"]}
        for label, lr in zip("AB", _audio(rep, engine)):
            mono = _mono(lr)
            row[label] = [{"level_db": _level(mono, t, t + 0.04),
                           "centroid_hz": _centroid(mono, t, t + 0.046)}
                          for t in times]

        def spread(rows, key):
            values = [r[key] for r in rows if r[key] is not None]
            return round(max(values) - min(values), 2) if values else None
        row["level_range_db"] = {"A": spread(row["A"], "level_db"),
                                 "B": spread(row["B"], "level_db")}
        row["centroid_range_hz"] = {"A": spread(row["A"], "centroid_hz"),
                                    "B": spread(row["B"], "centroid_hz")}
        la, lb = row["level_range_db"]["A"], row["level_range_db"]["B"]
        ca, cb = row["centroid_range_hz"]["A"], row["centroid_range_hz"]["B"]
        row["holds"] = bool(la is not None and lb is not None
                            and ca is not None and cb is not None
                            and (la >= lb + 2.0 or ca >= 2 * cb + 50))
        out[engine] = row
    return out


def check_bumps(rep, card):
    """The ten-node envelope makes one note fall and rise again (towards
    ticks 13, 33, 49 and 68) before its long tail: after the attack (the
    first 150 ms, where the sample's own onset rises too) A's level rises
    3 dB or more at least three separate times; B (the envelope off) not
    once."""
    out = {}
    for engine in _engines(rep):
        row = {}
        for label in "AB":
            track = rep["engines"][engine][label]["level_track_db"]
            rises, low = [], None
            for i, v in enumerate(track):
                if i * 10 < 150:
                    continue
                if low is None or v < low[1]:
                    low = (i, v)
                elif v - low[1] >= 3.0 and v > -90:
                    if rises and i * 10 - rises[-1]["ms"] <= 100:
                        rises[-1]["rise_db"] = round(
                            rises[-1]["rise_db"] + v - low[1], 1)
                    else:
                        rises.append({"ms": i * 10,
                                      "rise_db": round(v - low[1], 1)})
                    low = (i, v)
            row[label] = {"rises": rises}
        row["holds"] = bool(len(row["A"]["rises"]) >= 3
                            and not row["B"]["rises"])
        out[engine] = row
    return out


NARRATIVE = {
 "filter_sweep_bass": {
  "title": "A resonant filter swept by the instrument over a held note",
  "kind": "instrument technique (IT)",
  "instrument": 2,
  "purpose": "A bass that changes colour while it holds, on one sample: the movement is the instrument's, not the PCM's. Instrument 1 (ChipBass.raw) plays the same sample with no filter; instrument 2 adds a filter envelope that lowers the cutoff over five seconds, with the resonance high (115), so what is heard is a resonant peak travelling down through the harmonics.",
  "b_note": "B clears the envelope's on bit and its filter mark (flags 0x81 -> 0x00). With the on bit alone cleared the two players disagree: Schism Tracker still lowers the filter, as if the envelope stood at its middle value (a resonant peak near 820 Hz), libopenmpt does not; with both cleared they agree."
 },
 "echo_in_the_envelope": {
  "title": "An echo written into the instrument's envelopes (checked in place)",
  "kind": "instrument technique (IT)",
  "instrument": 10,
  "purpose": "An echo that belongs to the instrument: three strikes in the volume envelope, the repeats thrown right and then left by the pan envelope and darkened by the filter envelope, NNA note off so a note's repeats sound under the next notes. Here the line runs down in steps of two rows and stops, and its echoes go on after it, alternating sides.",
  "b_note": "B keeps the volume envelope's first three nodes (64@0 13@5 0@15: the dry strike) and drops the repeats: one byte, the node count 9 -> 3."
 },
 "filter_carry": {
  "title": "A filter envelope that carries across held notes",
  "kind": "instrument technique (IT)",
  "instrument": 2,
  "purpose": "The filter sweep marks the start of a phrase, not every note: after a key-off the next note opens the filter again from dark (about 30 dB of upper band in 200 ms); a note that follows a held note keeps the filter where the last one left it. Measured rule, both players: carry continues the envelope only while the previous note of the instrument is still held; a key-off or a note cut starts it again.",
  "b_note": "B clears the carry bit (flags 137 -> 129): every note sweeps."
 },
 "octave_trill_envelope": {
  "title": "An octave trill the instrument plays by itself",
  "kind": "instrument technique (IT)",
  "instrument": 15,
  "purpose": "The pattern holds one note; the instrument's looped pitch envelope throws it up an octave and back every 6 ticks (125 ms here), while the volume envelope lets it fade. An arpeggio that costs no effect column.",
  "b_note": "B switches the pitch envelope off (flags 3 -> 2: the loop bit stays, the envelope does not run)."
 },
 "vibrato_memory_under_slides": {
  "title": "A vibrato carried on under volume slides (K after H)",
  "kind": "pattern technique (S3M)",
  "purpose": "One H93 sets the vibrato (speed 9, depth 3); the K commands after it keep that vibrato going while they fade the note (K is vibrato with the last H's parameters plus a volume slide). The fade and the wobble are one gesture, in one effect column.",
  "b_note": "B changes each K to D with the same parameter (one byte a cell): the slides stay, the vibrato stops after the H row."
 },
 "falling_repeats": {
  "title": "Repeats of one note, each quieter and each sliding down",
  "kind": "pattern technique (S3M)",
  "purpose": "An echo made by hand: the note is struck again on the same channel two and three rows later at lower volume (v14 = 20, v0A = 10, in hex as libopenmpt prints them), and E0F pulls each strike down while it sounds.",
  "b_note": "B clears the E0F commands (command and parameter, two bytes a cell)."
 },
 "volume_gate": {
  "title": "A held note pulsed by alternating volume slides and resets",
  "kind": "pattern technique (S3M)",
  "purpose": "One D-6 held for the whole pattern; D0F on the odd rows pulls it down (64 to 34 over a row of three ticks) and v40 (64) on the even rows puts it back: a tremolo at the row rate, 120 ms a cycle, on top of the sample's own pulse.",
  "b_note": "B clears the D0F commands; the v40 resets stay."
 },
 "attack_variants": {
  "title": "One pitch struck with three different instruments",
  "kind": "pattern technique (S3M)",
  "purpose": "The same G#6 every two or four rows, with instruments 21, 22 and 23 in turn: three samples for one hit, so the repeats do not sound identical (22 and 23 are 3.7 to 5 dB louder than 21 and differ in brightness).",
  "b_note": "B sets every strike's instrument to 21 (one byte a cell)."
 },
 "ten_node_decay": {
  "title": "A ten-node volume envelope: a note that falls and rises again",
  "kind": "instrument technique (IT)",
  "instrument": 18,
  "purpose": "The one enabled envelope among the module's 18 instruments: 64 -> 11 -> 17 -> 6 -> 12 -> 3 -> 8 -> 1 -> 3, then a tail to 0 at tick 344 (5.5 s). One note decays in steps that rise again (a built-in echo), and the long tail is cut by the next note (NNA cut, fade-out 8). The chord is spread over three channels two rows apart, and channel 1 alternates speed 6 and 5 (A06 A05): a swing written into the row lengths.",
  "b_note": "B switches the volume envelope off (flags 1 -> 0): the sample plays at full level."
 }
}


CARDS = [
    {"id": "filter_sweep_bass", "cells_rows": 32, "file": "Skaven252/skaven-fourth_symmetriad.it",
     "what": "instrument 2 (ChipBass.sweep): the same sample as instrument "
             "1, with a filter envelope that closes over five seconds",
     "order": 1, "row": 96, "seconds": 2.5, "channels": [2],
     "band_times_s": [0.1, 0.6, 1.2, 1.8, 2.3],
     "pairs": [{"name": "filter envelope off",
                "changes": ["inst 2 fenv off"], "check": check_sweep}]},
    {"id": "echo_in_the_envelope", "cells_rows": 16,
     "file": "Skaven252/skaven-fourth_symmetriad.it",
     "what": "instrument 10 (Sine.softecho): three strikes in the volume "
             "envelope, the repeats thrown right and left by the pan "
             "envelope, NNA note off",
     "order": 22, "row": 0, "seconds": 1.85, "channels": [11],
     "tail_s": 1.3,
     "pairs": [{"name": "repeats cut (volume envelope kept to its first "
                        "3 nodes)",
                "changes": ["inst 10 venv nodes 3"], "check": check_echo}]},
    {"id": "filter_carry", "cells_rows": 64, "file": "Manwe/new_wind_in_syworld.it",
     "what": "instrument 2 (Distortion): a filter envelope with carry, "
             "NNA cut",
     "order": None, "pattern": 6, "row": 0, "seconds": 4.0, "channels": [5],
     "note_rows": [12, 18, 44, 50],
     "after": ["key-off", "note", "key-off", "note"],
     "pairs": [{"name": "carry off", "changes": ["inst 2 fenv carry off"],
                "check": check_carry}]},
    {"id": "octave_trill_envelope", "cells_rows": 16, "file": "Manwe/new_wind_in_syworld.it",
     "what": "instrument 15 (Sin): a looped pitch envelope, a note and its "
             "octave every 6 ticks",
     "order": None, "pattern": 5, "row": 0, "seconds": 1.0, "channels": [9],
     "pairs": [{"name": "pitch envelope off",
                "changes": ["inst 15 ienv off"], "check": check_trill,
                "pitch": True}]},
    {"id": "vibrato_memory_under_slides",
     "file": "FearofDark/dancinginthetube.s3m",
     "what": "channel 5: H93 then K02 K03 K04 K04 — the vibrato carried on "
             "under volume slides",
     "order": 3, "row": 8, "seconds": 0.96, "channels": [5],
     "measure_s": [0.25, 0.70],
     "pairs": [{"name": "K -> D (slides kept, vibrato not)",
                "changes": ["cell 3 10 5 effect D", "cell 3 11 5 effect D",
                            "cell 3 12 5 effect D", "cell 3 13 5 effect D"],
                "check": check_vibrato, "pitch": True}]},
    {"id": "falling_repeats", "file": "FearofDark/dancinginthetube.s3m",
     "what": "channel 6: A#6, E0F, E0F, the note again quieter, E0F ... — "
             "each repeat slides down",
     "order": 3, "row": 8, "seconds": 0.96, "channels": [6],
     "slides_s": [[0.12, 0.36], [0.48, 0.72]],
     "pairs": [{"name": "E0F cleared",
                "changes": ["cell 3 9 6 effect none", "cell 3 10 6 effect none",
                            "cell 3 12 6 effect none", "cell 3 13 6 effect none",
                            "cell 3 15 6 effect none"],
                "check": check_slide}]},
    {"id": "volume_gate", "cells_rows": 16, "file": "Necros/isotoxin.s3m",
     "what": "channel 6: one D-6 held, D0F and v40 on alternate rows",
     "order": None, "pattern": 52, "row": 0, "seconds": 0.96, "channels": [6],
     "pairs": [{"name": "D0F cleared",
                "changes": [f"cell 52 {r} 6 effect none"
                            for r in range(1, 16, 2)],
                "check": check_gate}]},
    {"id": "attack_variants", "cells_rows": 16, "file": "Necros/isotoxin.s3m",
     "what": "channel 8: G#6 struck with instruments 21, 22, 21, 21, 23, 22",
     "order": None, "pattern": 52, "row": 0, "seconds": 0.96, "channels": [8],
     "note_rows": [2, 4, 6, 10, 12, 14], "instruments": [21, 22, 21, 21, 23, 22],
     "pairs": [{"name": "every strike instrument 21",
                "changes": ["cell 52 4 8 instrument 21",
                            "cell 52 12 8 instrument 21",
                            "cell 52 14 8 instrument 21"],
                "check": check_variants}]},
    {"id": "ten_node_decay", "file": "FearofDark/fod_rosethorn.it",
     "what": "instrument 18: the one enabled envelope of the module, ten "
             "nodes, a note that falls and rises four times",
     "order": 1, "row": 0, "seconds": 1.6, "channels": [1],
     "pairs": [{"name": "volume envelope off",
                "changes": ["inst 18 venv off"], "check": check_bumps}]},
]


def _order_of(data, pattern):
    with corpus.Module(data) as module:
        return module.orders().index(pattern)


def _mp3(a, b, path, gap=0.5):
    from array import array
    pause = [0.0] * int(gap * RATE)
    left = a[0] + pause + b[0]
    right = a[1] + pause + b[1]
    frames = array("f")
    for x, y in zip(left, right):
        frames.append(x)
        frames.append(y)
    wav = path[:-4] + ".wav"
    openmpt.write_wav(frames, wav, RATE)
    try:
        R.to_mp3(wav, path)
    finally:
        os.remove(wav)
    return os.path.basename(path)


def _slim(entry):
    """The report without the long tracks of B (A's are kept to read)."""
    out = dict(entry)
    for label in ("A", "B"):
        f = dict(out[label])
        if label == "B":
            f.pop("level_track_db", None)
            f["centroid_hz"] = {k: v for k, v in f["centroid_hz"].items()
                                if k != "track"}
            f["balance_db"] = {k: v for k, v in f["balance_db"].items()
                               if k != "track"}
        out[label] = f
    return out


# -- cards ---------------------------------------------------------------------
def _summary_line(card_id, verdict):
    """One sentence per engine with the numbers the check turned on."""
    out = {}
    for engine, v in verdict.items():
        if card_id == "filter_sweep_bass":
            a, b = v["A"], v["B"]
            text = (f"A: the 1.2-5 kHz band spans {a['span_db']['high_1200_5000']} "
                    f"dB along the note (peak near "
                    f"{a['bands'][-2]['peak_hz']} Hz at "
                    f"{a['bands'][-2]['at_s']} s, {a['bands'][-1]['peak_hz']} Hz "
                    f"at {a['bands'][-1]['at_s']} s); B: every band within "
                    f"{max(b['span_db'].values())} dB")
        elif card_id == "echo_in_the_envelope":
            text = (f"after the line stops, A strikes {len(v['tail_strikes']['A'])} "
                    f"more times ({', '.join(f'{x:+.1f}' for x in v['tail_sides_db'])}"
                    f" dB right of the dry note), B none; tail level A "
                    f"{v['tail_level_db']['A']} dB, B {v['tail_level_db']['B']} "
                    f"dB; first strike {v['first_strike_db']['A']} / "
                    f"{v['first_strike_db']['B']} dB")
        elif card_id == "filter_carry":
            rows = [f"row {n['row']} after a {n['after']}" for n in v["notes"]]
            text = "; ".join(
                f"{r}: A {a:+.1f} dB, B {b:+.1f} dB"
                for r, a, b in zip(rows, v["A"]["sweep_db"],
                                   v["B"]["sweep_db"]))
            text = "upper-band sweep in the first 240 ms — " + text
        elif card_id == "octave_trill_envelope":
            text = (f"A spends {v['A']['share_up']:.0%} of its voiced 10 ms "
                    f"steps an octave up, B {v['B']['share_up']:.0%}")
        elif card_id == "vibrato_memory_under_slides":
            text = (f"over the K rows the pitch spreads {v['A']['spread_cents']} "
                    f"cents in A (range {v['A']['range_cents']}), "
                    f"{v['B']['spread_cents']} in B (range "
                    f"{v['B']['range_cents']})")
        elif card_id == "falling_repeats":
            text = (f"centroid at the end of each slide over its start: A "
                    f"{', '.join(map(str, v['A']['last_over_first']))}; B "
                    f"{', '.join(map(str, v['B']['last_over_first']))}")
        elif card_id == "volume_gate":
            text = (f"odd rows minus even rows: A {v['A']['odd_minus_even_db']}"
                    f" dB, B {v['B']['odd_minus_even_db']} dB")
        elif card_id == "attack_variants":
            text = (f"strike levels span {v['level_range_db']['A']} dB in A, "
                    f"{v['level_range_db']['B']} dB in B; centroids span "
                    f"{v['centroid_range_hz']['A']} Hz in A, "
                    f"{v['centroid_range_hz']['B']} Hz in B")
        elif card_id == "ten_node_decay":
            text = (f"A rises again at "
                    f"{', '.join(str(r['ms']) for r in v['A']['rises'])} ms "
                    f"({', '.join(str(r['rise_db']) for r in v['A']['rises'])}"
                    f" dB), B {len(v['B']['rises'])} times")
        else:
            text = ""
        out[engine] = text
    return out


def _native(card, data, address):
    """What the module writes there: the instrument as the players run it,
    or the card's channels' cells, as libopenmpt prints them."""
    narrative = NARRATIVE[card["id"]]
    lines = []
    if narrative.get("instrument"):
        from schism import it_read
        from schism.forge import program
        number = narrative["instrument"]
        info = program.explain_instrument(
            it_read.read(data, load_data=False), number, address["tempo"])
        lines.append(f"instrument {number} \"{info['name'].strip()}\", at "
                     f"tempo {address['tempo']} (a tick is "
                     f"{info['tick_ms']} ms):")
        lines.append("")
        lines.append("| envelope | nodes (value@tick) | loop | sustain | carry |")
        lines.append("|---|---|---|---|---|")
        for key in ("volume", "pan", "pitch_or_filter"):
            env = info[key]
            if not env:
                continue
            nodes = " ".join(f"{n['value']}@{n['tick']}" for n in env["nodes"])
            if env["slot"] == "filter":
                nodes += (" (file values, -32..32: " + " ".join(
                    f"{n['value'] - 32}@{n['tick']}" for n in env["nodes"])
                          + ")")
            lines.append(f"| {env['slot']} | {nodes} | {env['loop'] or '-'} "
                         f"| {env['sustain'] or '-'} | "
                         f"{'yes' if env.get('carry') else 'no'} |")
        lines.append("")
        lines.append(f"NNA {info['nna']}, fade-out {info['fadeout']}, cutoff "
                     f"{info['cutoff']}, resonance {info['resonance']}.")
    with corpus.Module(data) as module:
        rows = card.get("cells_rows", 8)
        lines.append("")
        lines.append(f"The channel{'s' if len(card['channels']) > 1 else ''} "
                     f"at the address (libopenmpt's text; instrument and "
                     f"volume in hex):")
        lines.append("")
        lines.append("```text")
        for r in range(address["row"], min(module.rows(address["pattern"]),
                                           address["row"] + rows)):
            cells = [module.native(address["pattern"], r, c - 1)
                     for c in card["channels"]]
            if all(c.replace(".", "").replace(" ", "") == ""
                   for c in cells):
                continue
            lines.append(f"row {r:3d}  " + " | ".join(cells))
        lines.append("```")
    return "\n".join(lines)


def write_cards(report, corpus_dir, out_dir, zip_sha256=None):
    """One Markdown card per technique and `reference_ab.json`, the readings
    without their tracks."""
    os.makedirs(out_dir, exist_ok=True)
    compact = {"format": "schism-reference-cards/1",
               "engines": report["engines"], "rate": report["rate"],
               "corpus_zip_sha256": zip_sha256, "cards": []}
    index = []
    by_id = {c["id"]: c for c in CARDS}
    for entry in report["cards"]:
        card = by_id[entry["card"]]
        narrative = NARRATIVE[card["id"]]
        path = os.path.join(corpus_dir, "modules", card["file"])
        with open(path, "rb") as handle:
            data = handle.read()
        for pair in entry["pairs"]:
            rep = pair["report"]
            address = rep["address"]
            summary = _summary_line(card["id"], pair["verdict"])
            holds = {e: v["holds"] for e, v in pair["verdict"].items()}
            md = [f"# Card: {narrative['title']}", "",
                  f"**Kind:** {narrative['kind']}. **Checked:** in the "
                  f"module itself, A against B in "
                  f"{' and '.join(holds)}; the hypothesis "
                  + ("held in both." if all(holds.values()) else
                     "did not hold everywhere: " + json.dumps(holds) + ".")
                  + " Nobody listened for this card: it says what the "
                    "mechanism does, not whether it is good.", "",
                  "## Where it was found", "",
                  "| | |", "|---|---|",
                  f"| module | {entry['file']} (SHA-256 "
                  f"`{rep['hashes']['original'][:16]}…`) |",
                  f"| address | order {address['order']}, pattern "
                  f"{address['pattern']}, row {address['row']}, channel"
                  f"{'s' if len(address['channels']) > 1 else ''} "
                  f"{', '.join(map(str, address['channels']))}; the song gets "
                  f"there at {address['at']} s (libopenmpt, 44.1 kHz) |",
                  f"| clock there | speed {address['speed']}, tempo "
                  f"{address['tempo']}: a tick is {address['tick_ms']} ms, a "
                  f"row {address['row_ms']} ms |",
                  f"| source | the reference corpus "
                  + (f"(zip SHA-256 `{zip_sha256[:16]}…`)" if zip_sha256 else "")
                  + "; authorship and licence as its catalogue gives them "
                    "(Mod Archive Distribution license: personal study) |",
                  "", "## What is written there", "",
                  _native(card, data, address), "",
                  "## What it is for (interpretation)", "",
                  narrative["purpose"], "",
                  "## The A/B", "",
                  narrative["b_note"], "", "Bytes B changes in its copy:", "",
                  "| offset | old | new | why |", "|---|---|---|---|"]
            for b in rep["change"]["bytes"]:
                md.append(f"| {b['offset']} | {b['old']} | {b['new']} | "
                          f"{b['why']} |")
            md += ["",
                   f"Read for {address['seconds']} s from the address, "
                   f"alone (libopenmpt: {rep['isolation']['openmpt']}; "
                   f"Schism Tracker: {rep['isolation']['schism']}) and in "
                   f"the mix. The original file's hash is the same after "
                   f"the run: {rep['original_untouched']}."
                   + (f" Instruments {', '.join(map(str, rep['randomness']['instruments'][:6]))}"
                      f"{'…' if len(rep['randomness']['instruments']) > 6 else ''} of "
                      f"this module vary their volume or pan at random, so the "
                      f"readings in the mix move a little from run to run "
                      f"(those of the channel alone do not, unless it plays "
                      f"one of them)."
                      if rep.get("randomness", {}).get("instruments") else ""),
                   "",
                   "## What was measured", "",
                   pair["check"], ""]
            for engine, line in summary.items():
                e = rep["engines"][engine]
                extra = (f"; A - B alone {e['null_isolated_db']} dB against "
                         f"A")
                if e.get("null_in_context_db") is not None:
                    extra += (f", in the mix {e['null_in_context_db']} dB")
                if e.get("lag_ms"):
                    extra += (f"; Schism's render aligned to libopenmpt's "
                              f"by {e['lag_ms']['A']} ms")
                md.append(f"- **{engine}** ({'holds' if holds[engine] else 'does not hold'}): "
                          f"{line}{extra}.")
            md += ["", "Listen: `" + pair["files"]["isolated"] + "` (alone, A "
                   "then B) and `" + pair["files"]["in_context"] + "` (in the "
                   "mix), in the training archive's reference folder.", "",
                   "Reproduce: `python3 examples/reference/make.py CORPUS_DIR "
                   f"OUT_DIR {card['id']}`, or one pair by hand: "
                   f"`python3 -m schism corpus ab {os.path.basename(card['file'])} "
                   f"--order {address['order']} --row {address['row']} "
                   f"--seconds {address['seconds']} --channels "
                   f"{','.join(map(str, address['channels']))} "
                   + " ".join(f"--change '{c}'" for c in rep["change"]["asked"])
                   + "`.", ""]
            name = f"{card['id']}.md"
            with open(os.path.join(out_dir, name), "w",
                      encoding="utf-8") as handle:
                handle.write("\n".join(md))
            index.append((card["id"], narrative["title"], entry["file"],
                          holds))
            compact["cards"].append({
                "id": card["id"], "title": narrative["title"],
                "kind": narrative["kind"], "file": entry["file"],
                "sha256": rep["hashes"]["original"], "address": address,
                "change": rep["change"], "check": pair["check"],
                "verdict": {e: {k: v for k, v in d.items()
                                if k not in ("bands",)}
                            for e, d in pair["verdict"].items()},
                "summary": summary,
                "null_db": {e: {"alone": d["null_isolated_db"],
                                "in_mix": d.get("null_in_context_db")}
                            for e, d in rep["engines"].items()},
                "files": pair["files"]})
    with open(os.path.join(out_dir, "reference_ab.json"), "w",
              encoding="utf-8") as handle:
        json.dump(compact, handle, indent=1, ensure_ascii=False)
    return index


def main(corpus_dir, out_dir, only=None):
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    results = []
    for card in CARDS:
        if only and card["id"] not in only:
            continue
        path = os.path.join(corpus_dir, "modules", card["file"])
        with open(path, "rb") as handle:
            data = handle.read()
        order = card["order"]
        if order is None:
            order = _order_of(data, card["pattern"])
        entry = {"card": card["id"], "file": card["file"],
                 "what": card["what"], "pairs": []}
        for pair in card["pairs"]:
            rep = abtest.run(data, order, card["row"], card["seconds"],
                             pair["changes"], card["channels"],
                             pitch=pair.get("pitch", False), keep_audio=True)
            rep["_rows"] = abtest.locate(data, order, card["row"])["rows"]
            verdict = pair["check"](rep, card)
            stem = f"{card['id']}"
            files = {"isolated": _mp3(*_audio(rep, "openmpt"),
                                      os.path.join(out_dir,
                                                   f"{stem}_alone_A_then_B.mp3")),
                     "in_context": _mp3(rep["audio"]["openmpt_mix"]["A"],
                                        rep["audio"]["openmpt_mix"]["B"],
                                        os.path.join(out_dir,
                                                     f"{stem}_mix_A_then_B.mp3"))}
            rep.pop("audio")
            rep.pop("_rows")
            rep["engines"] = {e: _slim(v) for e, v in rep["engines"].items()}
            entry["pairs"].append({"name": pair["name"],
                                   "check": " ".join(
                                       pair["check"].__doc__.split()),
                                   "verdict": verdict, "files": files,
                                   "report": rep})
            holds = {e: v["holds"] for e, v in verdict.items()}
            print(f"{card['id']:28s} {pair['name'][:40]:40s} holds {holds}  "
                  f"null in mix {rep['engines']['openmpt'].get('null_in_context_db')} dB",
                  flush=True)
        results.append(entry)
    report = {"format": "schism-reference-ab/1",
              "engines": {"openmpt": openmpt.version(),
                          "schism": __import__("schism.schismtracker",
                                               fromlist=["version"]).version()},
              "rate": RATE, "cards": results,
              "seconds": round(time.time() - started, 1)}
    with open(os.path.join(out_dir, "report.json"), "w") as handle:
        json.dump(report, handle, indent=1)
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--cards=")]
    cards = [a.split("=", 1)[1] for a in sys.argv[1:]
             if a.startswith("--cards=")]
    if len(args) < 2:
        print(__doc__)
        sys.exit(2)
    result = main(args[0], args[1], args[2:] or None)
    if cards:
        import hashlib
        zips = [os.path.join(args[0], n) for n in os.listdir(args[0])
                if n.endswith(".zip")]
        digest = (hashlib.sha256(open(zips[0], "rb").read()).hexdigest()
                  if zips else os.environ.get("CORPUS_ZIP_SHA256"))
        write_cards(result, args[0], cards[0], digest)
