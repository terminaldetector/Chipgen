"""Two forge instruments, one targeted change each, measured and played as A/B.

    bass  fm_punch_bass, "darker sustain": the attack stays bright and the
          body darkens, a modulator-level program over the note (FM timbre
          that develops inside the note)
    lead  fm_saw_lead, "legato": one key-on per run of touching notes, the
          pitch moved for the rest (articulation; the key-off ends a run)

Both come from the shipped starter bank and both changes are found by the
bounded loop (synthesis/improve.py, 3 candidates x 2 rounds), not written
by hand. The melodies are this file's own and are the same in A and B.

    python3 examples/programs/make.py [OUT_DIR]

writes bank.json (each instrument as it was and as improved), the two run
records, the A/B of each part alone (A then B, at the levels they play at,
and with B matched to A's loudness), and a short etude with both parts, a
pad and drums, before and after, as MP3, VGZ (the register log a VGM player
or Furnace opens) and tracker text. report.json has the readings and the
costs. Whether B sounds better is for listening; the readings say whether
the change did what it was meant to, in the part and in the mix.
"""

import copy
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "python"))

import analysis  # noqa: E402
import chipgen  # noqa: E402
import events as E  # noqa: E402
from synthesis import bank as B, improve as I, phases, program as P  # noqa: E402

BANK = os.path.join(ROOT, "python", "synthesis", "banks", "starter.bank.json")

#: two bars a pattern at 120 BPM, 4 rows a beat; 240 ticks a second so the
#: programs' 60 Hz writes land exactly on ticks
HEAD = "bpm 120\nlpb 4\nticks 240\n"

BASS_LINE = ["A-1", None, "A-1", "E-2", None, "A-1", "G-1", None,
             "F-1", None, "F-1", "C-2", None, "F-1", "E-1", "===",
             "D-1", None, "D-1", "A-1", None, "D-1", "C-1", None,
             "E-1", None, "E-1", "B-1", None, "E-1", "E-1", "==="]
#: touching notes (a legato run), a repeated note, a leap, a held note with
#: a key-off, a gap: the lead's own line
LEAD_LINE = ["E-4", "G-4", "A-4", "B-4", "C-5", None, "B-4", "A-4",
             "G-4", None, "===", None, "A-4", "C-5", "E-5", None,
             "D-5", "C-5", "B-4", "A-4", None, None, "===", None,
             "E-4", "F#4", "G#4", None, "B-4", None, None, "==="]


def _rows(line):
    return ["..." if c is None else c for c in line]


def part_score(column, name, line, rows_pad=4):
    cells = _rows(line) + ["..."] * rows_pad
    return HEAD + f"inst {column} {name}\ncols {column}\n" + "\n".join(cells) \
        + "\n"


def etude_score(bass, lead):
    """The parts together: bass fm0, lead fm1 (its detune layer takes fm2),
    a pad on fm3, drums on the DAC. Two passes of the 32 rows."""
    bass_cells = _rows(BASS_LINE) * 2
    lead_cells = _rows(LEAD_LINE) * 2
    rows = []
    for i in range(64):
        pad = ("A-3" if i == 0 else "F-3" if i == 32 else
               "===" if i in (31, 63) else "...")
        drum = ("kick" if i % 8 == 0 else "snare" if i % 8 == 4 else
                "hat" if i % 2 == 0 else "...")
        rows.append(f"{bass_cells[i]:5s} {lead_cells[i]:5s} {pad:5s} {drum}")
    return (HEAD + f"inst fm0 {bass}\ninst fm1 {lead}\ninst fm3 strings\n"
            "vol fm3 56\ncols fm0 fm1 fm3 dac\n" + "\n".join(rows)
            + "\n" + "\n".join(["...   ...   ...   ..."] * 6) + "\n")


def mono_of(result):
    return list(analysis.to_mono(result.audio))


def rms(x):
    return math.sqrt(sum(v * v for v in x) / len(x)) if x else 0.0


def db(v):
    return 20.0 * math.log10(v) if v > 1e-12 else -240.0


def write_pair(a, b, rate, path_stem, gain_b=1.0):
    """A, half a second of silence, B: as WAV and MP3."""
    import audio
    import mp3 as mp3_mod
    import wavio
    gap = [0.0] * int(0.5 * rate)
    joined = a + gap + [v * gain_b for v in b]
    buf = audio.from_floats(joined, channels=1)
    wavio.write(path_stem + ".wav", buf, rate)
    mp3_mod.encode(buf, rate, path_stem + ".mp3")


def context(text, part_channel, rate=44100):
    """The part alone and everything else, from one score: for the margins
    of the part's attacks and bodies over the rest of the mix."""
    events, _, meta = chipgen.to_events(text)
    tps = meta.ticks_per_second

    def keep(ev, solo):
        if isinstance(ev, E.FMNoteOn):
            mine = ev.channel in (part_channel, part_channel + 1)
            return mine if solo else not mine
        if isinstance(ev, (E.DACSample, E.PSGToneOn, E.PSGNoiseOn)):
            return not solo
        return True
    solo = mono_of(chipgen.compose([e for e in events if keep(e, True)],
                                   ticks_per_second=tps))
    rest = mono_of(chipgen.compose([e for e in events if keep(e, False)],
                                   ticks_per_second=tps))
    now, notes, on = 0, [], None
    for ev in events:
        if isinstance(ev, E.Wait):
            now += ev.ticks
        elif isinstance(ev, E.FMNoteOn) and ev.channel == part_channel:
            if on is not None:
                notes.append((on, now / tps))
            on = now / tps
        elif isinstance(ev, E.FMNoteOff) and ev.channel == part_channel \
                and on is not None:
            notes.append((on, now / tps))
            on = None
    n = min(len(solo), len(rest))
    return phases.in_context(solo[:n], rest[:n], rate, notes)


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    started = time.time()
    starter = B.Bank.load(BANK)
    jobs = [("bass", "fm_punch_bass", "darker sustain", "fm0", BASS_LINE),
            ("lead", "fm_saw_lead", "legato", "fm1", LEAD_LINE)]
    bank = B.Bank("programs")
    report = {"parts": {}, "listening": "not done by this script: the "
              "readings say whether each change did what it was meant to; "
              "whether it sounds better is the A/B files' question"}
    renders = 0
    names = {}
    for label, source, intent, column, line in jobs:
        entry = copy.deepcopy(starter.get(source))
        record = I.improve(entry, intent)
        renders += record["costs"]["renders"]
        with open(os.path.join(out_dir, f"{label}.run.json"), "w") as handle:
            json.dump(record, handle, indent=1)
        base = copy.deepcopy(entry)
        base.name = base.compiled.name = f"{label}_base"
        better = copy.deepcopy(entry)
        better.name = better.compiled.name = f"{label}_{intent.replace(' ', '_')}"
        program = I.chosen_program(record)
        if program is None:
            raise SystemExit(f"{label}: the loop kept the baseline "
                             f"({record['stop']}); nothing to compare")
        better.compiled = P.attach(better.compiled, program)
        bank.add(base)
        bank.add(better)
        names[label] = (base.name, better.name)
        report["parts"][label] = {
            "instrument": source, "intent": intent,
            "hypothesis": record["hypothesis"],
            "change": [c["delta"] for r in record["rounds"]
                       for c in r["candidates"]
                       if c["id"] == record["best"]["id"]][0],
            "baseline": record["baseline"]["summary"],
            "improved": [c["summary"] for r in record["rounds"]
                         for c in r["candidates"]
                         if c["id"] == record["best"]["id"]][0],
            "gain": record["best"]["gain"], "stop": record["stop"],
            "loop_costs": record["costs"],
            "explain": P.explain(better.compiled, program, hold=0.5,
                                 tail=0.3, tps=240.0)}
    bank_path = bank.save(os.path.join(out_dir, "bank.json"))
    bank.install()
    # -- each part alone: A then B ------------------------------------------
    for label, _, _, column, line in jobs:
        base, better = names[label]
        a = chipgen.compose(part_score(column, base, line))
        b = chipgen.compose(part_score(column, better, line))
        renders += 2
        ma, mb = mono_of(a), mono_of(b)
        gain = rms(ma) / max(rms(mb), 1e-12)
        write_pair(ma, mb, a.sample_rate,
                   os.path.join(out_dir, f"ab_{label}_alone"))
        write_pair(ma, mb, a.sample_rate,
                   os.path.join(out_dir, f"ab_{label}_alone_matched"), gain)
        with open(os.path.join(out_dir, f"{label}_alone.trk"), "w") as handle:
            handle.write(part_score(column, "@INSTRUMENT", line))
        report["parts"][label]["alone"] = {
            "rms_db": {"A": round(db(rms(ma)), 2), "B": round(db(rms(mb)), 2)},
            "peak": {"A": round(max(abs(v) for v in ma), 3),
                     "B": round(max(abs(v) for v in mb), 3)},
            "matched_gain_db": round(db(gain), 2)}
    # -- the etude, before and after ----------------------------------------------
    report["etude"] = {}
    for tag, bass, lead in (("base", *(names[k][0] for k in ("bass", "lead"))),
                            ("improved", *(names[k][1]
                                           for k in ("bass", "lead")))):
        text = etude_score(bass, lead)
        stem = os.path.join(out_dir, f"etude_{tag}")
        with open(stem + ".trk", "w") as handle:
            handle.write(text)
        result = chipgen.compose(text, mp3=stem + ".mp3", vgm=stem + ".vgz",
                                 title=f"programs etude ({tag})")
        renders += 1
        mono = mono_of(result)
        report["etude"][tag] = {
            "seconds": round(len(mono) / result.sample_rate, 2),
            "peak": round(max(abs(v) for v in mono), 3),
            "rms_db": round(db(rms(mono)), 2),
            "clips": max(abs(v) for v in mono) >= 0.999,
            "bass_in_context": context(text, 0),
            "lead_in_context": context(text, 1),
            "warnings": result.warnings}
        renders += 4
    report["costs"] = {"renders": renders,
                       "wall_seconds": round(time.time() - started, 1),
                       "model_calls": 0, "model_tokens": None,
                       "note": "no model took part; the loop chose by its "
                               "readings"}
    report["bank"] = os.path.basename(bank_path)
    with open(os.path.join(out_dir, "report.json"), "w") as handle:
        json.dump(report, handle, indent=1)
    return report


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out")
    r = main(out)
    for label, part in r["parts"].items():
        print(f"{label}: {part['intent']} -> {part['change']} "
              f"(gain {part['gain']}, {part['loop_costs']['renders']} "
              f"renders, {part['loop_costs']['wall_seconds']} s)")
        print(f"   alone: {part['alone']}")
    for tag, e in r["etude"].items():
        print(f"etude {tag}: {e['seconds']} s peak {e['peak']} "
              f"bass attack margin {e['bass_in_context']['attack_margin_db']} "
              f"lead attack margin {e['lead_in_context']['attack_margin_db']}")
    print("costs:", r["costs"])
