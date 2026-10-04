"""make_demos.py — write the synthetic demo scores to demos/, then build them.

    python3 tools/make_demos.py              write the .sch files
    python3 tools/make_demos.py --build      also compile, calibrate loudness,
                                             write .it, and render WAV/MP3

"Synthetic" means two things here, and both are meant: every sound is
synthesised from a recipe (no recording is in the repository), and every
note is generated from musical rules (a mode, a chord progression, a
rhythm) rather than transcribed from a piece. The scores they produce are
ordinary `.sch` text — the thing a model would write — and that text, not
this script, is what is committed and compiled.
"""

import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import compose as C                                    # noqa: E402

DEMOS = os.path.join(ROOT, "demos")


# =========================================================================
# 1. Glass Engine — a synthwave track in A minor
# =========================================================================
def glass_engine() -> str:
    channels = 10
    KICK, SNARE, CLAP, HAT, OHAT = "C-2", "D-2", "D#2", "F#2", "A#2"
    progression = [  # (bass root, pad voicing, arp triad, bell note)
        ("A-1", ["A-3", "C-4", "E-4"], ["A-4", "C-5", "E-5", "A-5"], "A-6"),
        ("F-1", ["F-3", "A-3", "C-4"], ["F-4", "A-4", "C-5", "F-5"], "C-6"),
        ("C-2", ["C-4", "E-4", "G-4"], ["C-5", "E-5", "G-5", "C-6"], "G-6"),
        ("G-1", ["G-3", "B-3", "D-4"], ["G-4", "B-4", "D-5", "G-5"], "D-6"),
    ]
    # lead motifs: per bar, (row, note, effect) — chosen from the chord of
    # that bar, so every strong beat is a chord tone
    motif = {
        1: [[(0, "E-5", ""), (3, "D-5", ""), (6, "C-5", ""), (8, "E-5", ""),
             (12, "A-4", "")],
            [(0, "C-5", ""), (3, "A-4", ""), (6, "F-4", ""), (8, "A-4", ""),
             (12, "C-5", "")],
            [(0, "E-5", ""), (3, "G-5", ""), (6, "E-5", ""), (8, "D-5", ""),
             (12, "C-5", "")],
            [(0, "D-5", ""), (3, "B-4", ""), (6, "G-4", ""), (8, "B-4", ""),
             (11, "D-5", ""), (14, "E-5", "")]],
        2: [[(0, "A-5", ""), (4, "G-5", ""), (6, "E-5", ""), (8, "A-5", ""),
             (12, "C-6", "")],
            [(0, "A-5", ""), (4, "C-6", ""), (6, "A-5", ""), (8, "F-5", ""),
             (12, "A-5", "")],
            [(0, "G-5", ""), (4, "E-5", ""), (6, "G-5", ""), (8, "C-6", ""),
             (12, "B-5", "")],
            [(0, "B-5", ""), (3, "D-6", ""), (6, "B-5", ""), (8, "G-5", ""),
             (12, "D-5", ""), (14, "E-5", "")]],
        3: [[(0, "E-5", ""), (2, "A-5", ""), (4, "C-6", ""), (8, "B-5", ""),
             (10, "A-5", ""), (12, "E-5", "")],
            [(0, "F-5", ""), (2, "A-5", ""), (4, "C-6", ""), (8, "A-5", ""),
             (10, "F-5", ""), (12, "C-5", "")],
            [(0, "G-5", ""), (2, "C-6", ""), (4, "E-6", ""), (8, "D-6", ""),
             (10, "C-6", ""), (12, "G-5", "")],
            [(0, "D-6", ""), (2, "B-5", ""), (4, "G-5", ""), (8, "A-5", ""),
             (10, "B-5", ""), (12, "D-6", ""), (14, "E-6", "")]],
    }
    rng = random.Random(11)

    def build(name, *, kick=(0, 1, 2, 3), snare=False, hat=False, open_hat=False,
              ghost=False, bass=False, pad=True, lead=0, pluck=0, bell=False,
              riser=False, fill=False, last_chord_only=False):
        p = C.Pattern(name, 64, channels)
        for bar in range(4):
            base = bar * 16
            root, voicing, arp, bell_note = progression[bar]
            if bar in kick:
                for r in (0, 4, 8, 12):
                    p.put(base + r, 1, KICK, 1, "v64")
            if snare:
                for r in (4, 12):
                    p.put(base + r, 2, SNARE, 1, "v64")
            if hat:
                for r in (2, 6, 10):
                    p.put(base + r, 3, HAT, 1, "v46")
                p.put(base + 14, 3, OHAT if open_hat else HAT, 1,
                      "v48" if open_hat else "v42")
                if ghost:
                    for r in (1, 5, 9, 13):
                        p.put(base + r, 3, HAT, 1, "v22")
            if bass and bar in (0, 1, 2, 3):
                m = C.midi(root)
                for r, semis, v in ((0, 0, 64), (3, 0, 50), (6, 12, 56),
                                    (8, 0, 60), (10, 0, 48), (12, 7, 54),
                                    (14, 12, 52)):
                    p.put(base + r, 4, m + semis, 2, f"v{v}")
            if pad:
                for k, note in enumerate(voicing):
                    p.put(base, 5 + k, note, 3, "v60" if k == 0 else "v52")
            if lead:
                last = None
                for r, note, _ in motif[lead][bar]:
                    p.put(base + r, 8, note, 4, "v60" if r % 4 == 0 else "v50")
                    last = r
                if lead == 1 and bar == 3:
                    p.put(base + 15, 8, "===")
            if pluck:
                arp_cycle = (0, 1, 2, 3, 2, 1)
                for r in range(0, 16, 2 if pluck == 1 else 1):
                    note = arp[arp_cycle[(r // (2 if pluck == 1 else 1)) % 6]]
                    p.put(base + r, 9, note, 5, "v50" if r % 4 == 0 else "v34")
            if bell and bar in (0, 2):
                p.put(base, 10, bell_note, 6, "v50")
        if fill:
            for k, r in enumerate((56, 58, 60, 61, 62, 63)):
                p.put(r, 2, SNARE, 1, f"v{30 + 6 * k}")
            p.put(48, 2, CLAP, 1, "v60")
        if riser:
            p.put(32, 10, "C-4", 7, "v64")
            p.put(63, 10, "^^^")
        return p

    patterns = [
        build("intro", kick=(), pad=True, hat=False, bell=True),
        build("build", kick=(2, 3), hat=True, pluck=1, bell=True),
        build("a1", snare=True, hat=True, bass=True, pluck=1),
        build("a2", snare=True, hat=True, bass=True, pluck=1, lead=1),
        build("b1", snare=True, hat=True, open_hat=True, bass=True, pluck=2,
              lead=2, bell=True),
        build("b2", snare=True, hat=True, open_hat=True, bass=True, pluck=2,
              lead=2, bell=True, fill=True),
        build("break", kick=(), hat=False, pluck=1, bell=True, riser=True),
        build("c1", snare=True, hat=True, open_hat=True, ghost=True, bass=True,
              pluck=2, lead=3, bell=True),
        build("c2", snare=True, hat=True, open_hat=True, ghost=True, bass=True,
              pluck=2, lead=3, bell=True, fill=True),
        build("a3", snare=True, hat=True, bass=True, pluck=1, lead=1),
        build("outro1", kick=(0, 1), hat=True, pluck=1, bell=True),
        build("outro2", kick=(), hat=False, pluck=0, bell=True),
    ]
    head = C.header("Glass Engine", 118, channels, [
        "author schism-chipgen (synthetic)",
        "msg Synthetic demo: every sound is synthesised, every note",
        "msg is generated from a chord progression. Not a transcription.",
        "sep 110",
        "smp kick  wave=kick  len=0.34 vol=64",
        "smp snare wave=snare len=0.30 vol=64",
        "smp clap  wave=clap  vol=46",
        "smp hat   wave=hat   vol=48",
        "smp ohat  wave=openhat vol=46",
        "inst 1 name=Drum_Kit kit=C-2:kick,D-2:snare,D#2:clap,F#2:hat,A#2:ohat",
        "inst 2 name=Saw_Bass wave=saw oct=1-3 nna=cut cutoff=127 res=60 "
        "fenv=0:8,2:54,12:22,40:12 fade=60 vol=60 gain=51",
        "inst 3 name=Glass_Pad wave=pad voices=5 detune=13 secs=3 base=C-4 "
        "nna=fade fade=36 venv=0:0,36:64 penv=0:-14,180:14 cutoff=80 res=12 "
        "vol=36 gain=90",
        "inst 4 name=Sharp_Lead wave=pulse duty=0.3 oct=3-7 nna=cut cutoff=100 "
        "res=25 venv=0:0,2:64,40:56s,70:56s,95:0 vib=18/5/30/sine fade=48 "
        "vol=42 gain=90",
        "inst 5 name=Glass_Pluck wave=pluck root=C-5 len=1.2 damp=0.996 nna=cont "
        "dct=note dca=cut vol=40 gain=100",
        "inst 6 name=Bell wave=bell len=2.4 nna=cont vol=34 pan=44 gain=50",
        "inst 7 name=Riser wave=noise color=white len=1.0 venv=0:0,190:64 "
        "fenv=0:4,190:60 res=60 nna=cut vol=36",
        "chan 1 pan=32", "chan 2 pan=30", "chan 3 pan=40", "chan 4 pan=32",
        "chan 5 pan=18", "chan 6 pan=32", "chan 7 pan=46", "chan 8 pan=36",
        "chan 9 pan=24", "chan 10 pan=40",
    ])
    order = "order " + " ".join(p.name for p in patterns) + "\n"
    return head + "\n" + "\n".join(p.text() for p in patterns) + "\n" + order


# =========================================================================
# 2. First Light — chip-style, built on the effect column
# =========================================================================
def first_light() -> str:
    channels = 8
    LEAD, HARM, BASS, ARP, KICK, SNARE, HAT, FX = range(1, 9)
    prog_a = [("C-2", "C-4", "J47"), ("G-1", "G-3", "J47"),
              ("A-1", "A-3", "J37"), ("F-1", "F-3", "J47")]
    prog_b = [("A-1", "A-3", "J37"), ("F-1", "F-3", "J47"),
              ("C-2", "C-4", "J47"), ("G-1", "G-3", "J47")]
    hook_a = [[(0, "E-5", 2), (2, "G-5", 2), (4, "C-6", 4), (8, "B-5", 2),
               (10, "G-5", 2), (12, "E-5", 4)],
              [(0, "D-5", 2), (2, "G-5", 2), (4, "B-5", 4), (8, "A-5", 2),
               (10, "G-5", 2), (12, "D-5", 4)],
              [(0, "E-5", 2), (2, "A-5", 2), (4, "C-6", 2), (6, "B-5", 2),
               (8, "A-5", 2), (10, "E-5", 2), (12, "C-5", 4)],
              [(0, "C-5", 2), (2, "F-5", 2), (4, "A-5", 4), (8, "G-5", 2),
               (10, "F-5", 2), (12, "A-5", 2), (14, "G-5", 2)]]
    hook_b = [[(0, "A-5", 6), (6, "G-5", 2), (8, "E-5", 4), (12, "A-5", 4)],
              [(0, "F-5", 4), (4, "A-5", 4), (8, "C-6", 8)],
              [(0, "E-5", 2), (2, "G-5", 2), (4, "C-6", 4), (8, "E-6", 6),
               (14, "D-6", 2)],
              [(0, "D-6", 4), (4, "B-5", 4), (8, "G-5", 4), (12, "D-5", 4)]]

    def up(motif, semis):
        return [[(r, C.name(C.midi(n) + semis), ln) for r, n, ln in bar]
                for bar in motif]

    def build(name, prog, *, arp=True, bass=True, drums=True, lead=None,
              harmony=None, fill=False, laser=False, fade_pad=False,
              sparse=False):
        p = C.Pattern(name, 64, channels)
        for bar in range(4):
            base = bar * 16
            root, arp_root, j = prog[bar]
            if arp:
                for beat in range(4):
                    r = base + beat * 4
                    p.put(r, ARP, arp_root, 7, "v40" if beat % 2 == 0 else "v34",
                          j)
                    for k in range(1, 4):
                        p.put(r + k, ARP, fx="J00")
            if bass:
                m = C.midi(root)
                for r in range(0, 16, 2):
                    octave = 12 if r in (4, 12) else 0
                    p.put(base + r, BASS, m + octave, 3, "v62", "SC3")
            if drums:
                for r in (0, 8):
                    p.put(base + r, KICK, "C-5", 6, "v64")
                if bar % 2 == 1:
                    p.put(base + 11, KICK, "C-5", 6, "v56")
                for r in (4, 12):
                    p.put(base + r, SNARE, "C-4", 5, "v60")
                for r in range(0, 16, 2):
                    p.put(base + r, HAT, "C-5", 4,
                          "v44" if r % 4 == 2 else "v30")
            if lead:
                for r, note, length in lead[bar]:
                    p.put(base + r, LEAD, note, 1, "v62")
                    if length >= 4:                 # vibrato on the long ones
                        p.put(base + r + 2, LEAD, fx="H46")
                        for k in range(3, length):
                            p.put(base + r + k, LEAD, fx="H00")
            if harmony:
                for r, note, length in harmony[bar]:
                    p.put(base + r, HARM, note, 2, "v44")
        if fill:
            for k, r in enumerate((56, 58, 60, 62, 63)):
                p.put(r, SNARE, "C-4", 5, f"v{36 + 6 * k}", "Q23" if r == 62
                      else "...")
        if laser:
            p.put(48, FX, "C-6", 8, "v50", "F0C")
            for k in range(1, 4):
                p.put(48 + k, FX, fx="F00")
            p.put(60, FX, "C-6", 8, "v50", "E0C")
            for k in range(1, 4):
                p.put(60 + k, FX, fx="E00")
        return p

    third_below = lambda motif: up(motif, -4)
    patterns = [
        build("intro", prog_a, bass=False, drums=False),
        build("a1", prog_a),
        build("a2", prog_a, lead=hook_a),
        build("a3", prog_a, lead=up(hook_a, 12), harmony=third_below(hook_a)),
        build("b1", prog_b, lead=hook_b),
        build("b2", prog_b, lead=hook_b, harmony=third_below(hook_b),
              fill=True, laser=True),
        build("bridge", prog_b, bass=True, drums=False, arp=True,
              lead=up(hook_b, -12)),
        build("a4", prog_a, lead=hook_a, harmony=third_below(hook_a)),
        build("a5", prog_a, lead=up(hook_a, 12), harmony=third_below(hook_a),
              fill=True),
        build("outro", prog_a, drums=False, bass=True, lead=hook_a),
    ]
    head = C.header("First Light", 150, channels, [
        "author schism-chipgen (synthetic)",
        "msg Synthetic demo: chip-style, built on the effect column -",
        "msg J arpeggio, H vibrato, SC note cut, Q retrigger, E/F pitch slides.",
        "sep 90",
        "inst 1 name=Pulse_Lead wave=pulse duty=0.125 oct=3-7 nna=cut "
        "venv=0:64,3:60,40:56s,60:56s,80:0 fade=80 vol=46",
        "inst 2 name=Pulse_Harmony wave=pulse duty=0.5 oct=3-7 nna=cut "
        "venv=0:64,40:50s,60:50s,80:0 fade=80 vol=32 gain=57",
        "inst 3 name=Tri_Bass wave=triangle oct=1-4 nna=cut vol=64 fade=120 "
        "gain=102",
        "inst 4 name=Noise_Hat wave=noise len=0.5 venv=0:64,3:14,7:0 nna=cut "
        "vol=26",
        "inst 5 name=Noise_Snare wave=noise len=0.5 venv=0:64,5:34,16:0 "
        "nna=cut vol=40",
        "inst 6 name=Chip_Kick wave=kick start=150 end=46 len=0.2 drive=1.1 "
        "nna=cut vol=64",
        "inst 7 name=Pulse_Arp wave=pulse duty=0.25 oct=2-7 nna=cut "
        "venv=0:64,10:46,40:40s,60:40s,80:0 fade=100 vol=30 gain=72",
        "inst 8 name=Laser wave=pulse duty=0.5 oct=3-7 nna=cut "
        "venv=0:64,40:0 vol=28",
        "chan 1 pan=26", "chan 2 pan=40", "chan 3 pan=32", "chan 4 pan=32",
        "chan 5 pan=32", "chan 6 pan=32", "chan 7 pan=44", "chan 8 pan=20",
    ])
    order = "order " + " ".join(p.name for p in patterns) + "\n"
    return head + "\n" + "\n".join(p.text() for p in patterns) + "\n" + order


# =========================================================================
# 3. Pattern Garden — rhythm from Euclid, melody from a seeded walk
# =========================================================================
def pattern_garden() -> str:
    channels = 10
    KICK, SNARE, HAT, BASS, EP1, EP2, EP3, LEAD, BELL, TOM = range(1, 11)
    mode, root = "dorian", "D-3"
    chord_degrees = (0, 3, 4, 6)           # Dm  G  Am  C
    rng = random.Random(2026)

    def motif(seed, length_rows=32):
        """A two-bar motif: (row, scale-degree step). A random walk with a
        pull toward the chord tone, on an Euclidean rhythm."""
        r = random.Random(seed)
        hits = [i for i, on in enumerate(C.euclid(r.choice((5, 6, 7)), 16,
                                                  r.choice((0, 1, 2)))) if on]
        degree, out = r.choice((0, 2, 4)), []
        for bar in range(2):
            for i in hits:
                degree += r.choice((-2, -1, -1, 0, 1, 1, 2))
                degree = max(-2, min(9, degree))
                out.append((bar * 16 + i, degree))
        return out

    def build(name, *, kick=4, snare=True, hats=7, bass=True, ep=True, lead=None,
              lead_shift=0, bell=0.0, tom=False, seed=1):
        p = C.Pattern(name, 64, channels)
        r = random.Random(seed)
        for bar in range(4):
            base = bar * 16
            degree = chord_degrees[bar]
            chord = C.triad(root, mode, degree)
            if kick:
                for i, on in enumerate(C.euclid(kick, 16)):
                    if on:
                        p.put(base + i, KICK, "C-2", 1, "v64")
            if snare:
                for i, on in enumerate(C.euclid(2, 16, 4)):
                    if on:
                        p.put(base + i, SNARE, "D-2", 1, "v64")
            if hats:
                for i, on in enumerate(C.euclid(hats, 16, 1)):
                    if on:
                        p.put(base + i, HAT, "F#2", 1, f"v{r.choice((30, 36, 42, 48))}")
                p.put(base + 14, HAT, "A#2", 1, "v40")
            if bass:
                notes = (0, 0, 7, 12, 0)
                hits = [i for i, on in enumerate(C.euclid(5, 16)) if on]
                for k, i in enumerate(hits):
                    p.put(base + i, BASS, C.scale_note("D-1", mode, degree)
                          + notes[k % len(notes)], 2, "v60")
            if ep:
                inversion = bar % 3
                voiced = C.triad(root, mode, degree, inversion)
                hits = [i for i, on in enumerate(C.euclid(3, 16, bar)) if on]
                for i in hits:
                    for k, note in enumerate(voiced):
                        p.put(base + i, EP1 + k, note + 12, 3, "v46")
            if lead is not None:
                for row, step in lead:
                    if row // 16 == bar % 2:
                        n = C.scale_note("D-4", mode, step + degree // 2
                                         + lead_shift)
                        p.put(base + row % 16, LEAD, n, 4, "v56")
                p.put(base + 15, LEAD, "===")
            if bell and r.random() < bell:
                pent = C.scale_note("D-5", "minor_pentatonic",
                                    r.randrange(0, 5))
                p.put(base + r.choice((2, 6, 10, 14)), BELL, pent, 5, "v44")
        if tom:
            for k, row in enumerate((52, 54, 56, 58, 60, 62)):
                p.put(row, TOM, C.name(C.midi("A-4") - 3 * k), 6,
                      f"v{44 + 3 * k}")
        return p

    A, B, Cm = motif(101), motif(202), motif(303)
    patterns = [
        build("seed", kick=0, snare=False, hats=0, bass=False, bell=0.9, seed=1),
        build("sprout", kick=0, snare=False, hats=5, bass=False, bell=0.7, seed=2),
        build("root", kick=4, snare=False, hats=7, bass=True, bell=0.5, seed=3),
        build("stem", lead=A, bell=0.5, seed=4),
        build("leaf", lead=B, bell=0.6, seed=5),
        build("frost", kick=0, snare=False, hats=0, bass=False, lead=A,
              lead_shift=7, bell=1.0, seed=6),
        build("bloom", kick=5, hats=9, lead=Cm, bell=0.6, seed=7),
        build("seed2", kick=5, hats=9, lead=Cm, lead_shift=7, tom=True, seed=8),
        build("thorn", kick=4, hats=11, lead=B, bell=0.7, seed=9),
        build("canopy", kick=5, hats=9, lead=A, lead_shift=7, bell=0.8,
              tom=True, seed=10),
        build("wilt", kick=0, snare=False, hats=7, bass=False, lead=A,
              bell=0.9, seed=11),
        build("compost", kick=0, snare=False, hats=0, bass=False, ep=True,
              bell=0.9, seed=12),
    ]
    head = C.header("Pattern Garden", 124, channels, [
        "author schism-chipgen (synthetic)",
        "msg Synthetic demo: rhythms are Euclidean (Bjorklund), melodies are",
        "msg seeded random walks on D dorian pulled toward the chord.",
        "sep 100",
        "smp kick  wave=kick  start=150 end=48 len=0.30 vol=64",
        "smp snare wave=snare len=0.26 tone=200 vol=64",
        "smp hat   wave=hat   vol=64",
        "smp ohat  wave=openhat vol=56",
        "inst 1 name=Garden_Kit kit=C-2:kick,D-2:snare,F#2:hat,A#2:ohat",
        "inst 2 name=Root_Bass wave=square oct=1-3 nna=cut cutoff=127 res=40 "
        "fenv=0:30,3:56,20:22 fade=48 vol=56 gain=51",
        "inst 3 name=Glass_EP wave=fm ratio=1 index=2.2 oct=2-6 nna=cut "
        "venv=0:64,10:40,60:16,150:0 vol=36",
        "inst 4 name=Reed_Lead wave=square oct=3-7 nna=cut "
        "venv=0:0,2:64,30:50s,60:50s,100:0 vib=16/4/20/sine fade=40 "
        "cutoff=110 vol=34",
        "inst 5 name=Seed_Bell wave=bell len=2.4 nna=cont vol=30 pan=40",
        "inst 6 name=Tom wave=tom hz=150 len=0.45 nna=cut vol=50 gain=90",
        "chan 1 pan=32", "chan 2 pan=30", "chan 3 pan=44", "chan 4 pan=32",
        "chan 5 pan=20", "chan 6 pan=32", "chan 7 pan=44", "chan 8 pan=36",
        "chan 9 pan=46", "chan 10 pan=24",
    ])
    order = "order " + " ".join(p.name for p in patterns) + "\n"
    return head + "\n" + "\n".join(p.text() for p in patterns) + "\n" + order


DEMO_BUILDERS = {"glass_engine": glass_engine, "first_light": first_light,
                 "pattern_garden": pattern_garden}


def main(argv):
    only = [a for a in argv if not a.startswith("-")]
    os.makedirs(DEMOS, exist_ok=True)
    for key, builder in DEMO_BUILDERS.items():
        if only and key not in only:
            continue
        text = builder()
        path = os.path.join(DEMOS, key + ".sch")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"wrote {path} ({len(text) / 1024:.0f} KB)")
        if "--build" in argv:
            build_one(path)
    return 0


def build_one(path):
    from schism import it_write, notation, openmpt, render, verify
    stem = os.path.splitext(path)[0]
    module, report = notation.compile_file(path)
    print(report.text())
    peak = render.calibrate_mix_volume(module)
    print(f"mix volume {module.mix_volume} (peak {peak:.3f})")
    blob = it_write.build(module)
    problems = verify.compare(module, blob)
    print("libopenmpt reads it identically" if not problems else problems[:5])
    with open(stem + ".it", "wb") as handle:
        handle.write(blob)
    seconds, peak, encoder = render.render_mp3(
        blob, stem + ".mp3", title=module.title, artist="schism-chipgen",
        tail=1.0)
    print(f"{os.path.basename(stem)}: {seconds:.1f} s rendered, peak "
          f"{peak:.2f}, {encoder}")
    # keep the calibrated loudness in the committed score, replacing the
    # number a previous build left there
    text = open(path, encoding="utf-8").read()
    line = f"mv {module.mix_volume}"
    if re.search(r"^mv \d+[ \t]*$", text, re.M):
        new = re.sub(r"^mv \d+[ \t]*$", line, text, count=1, flags=re.M)
    else:
        new = text.replace("\nsep ", f"\n{line}\nsep ", 1)
    if new != text:
        open(path, "w", encoding="utf-8").write(new)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
