"""make_forge_demo.py — a song whose every sound came from the forge.

    python3 tools/make_forge_demo.py            write demos/forge_lab.sch
    python3 tools/make_forge_demo.py --build    also compile, set the loudness,
                                                write .it and .mp3

Nothing in the score is a recipe for a wave: the bass, the pads, the harp,
the lead, the bell, the stab, the riser, the impact and the whole drum kit
are `forge=` lines (the starter bank that ships with the forge), so the
score says what each sound is for and the forge says how it is made. The
notes are generated from a mode and a progression, as in the other demos.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import compose as C                                    # noqa: E402

DEMOS = os.path.join(ROOT, "demos")
CHANNELS = 13
(KICK, SNARE, HAT, PERC, BASS, PAD1, PAD2, PAD3, LEAD, HARP, BELL, STAB,
 FX) = range(1, 14)
INS = dict(kit=1, bass=2, pad=3, lead=4, harp=5, bell=6, stab=7, riser=8,
           impact=9, glass=10)
KEYS = dict(kick="C-2", snare="D-2", clap="D#2", hat="F#2", open="A#2",
            tom_low="F-2", tom_high="A-2", rim="C#2", crash="C#3",
            shaker="G#2", cowbell="G#3")
PROGRESSION = [(-1, 0), (-1, 5), (0, 2), (-1, 4)]       # roots as degrees of A minor
ROOT, MODE = "A-3", "minor"


def build():
    rows = 64
    bar = 16

    def chord(bar_index, inversion=0):
        octave_shift, degree = PROGRESSION[bar_index % 4]
        return C.triad(ROOT, MODE, degree, inversion), degree

    def pattern(name, kit=False, hats=False, snare=False, bass=False,
                pad=False, harp=False, lead=False, bell=False, stab=False,
                riser=False, impact=False, crash=False, fill=False,
                shaker=False):
        p = C.Pattern(name, rows, CHANNELS)
        for b in range(4):
            base = b * bar
            tones, degree = chord(b)
            root = C.scale_note(ROOT, MODE, degree) - 24
            if kit:
                for r in (0, 4, 8, 12):
                    p.put(base + r, KICK, KEYS["kick"], INS["kit"], "v64")
            if snare:
                for r in (4, 12):
                    p.put(base + r, SNARE, KEYS["snare"], INS["kit"], "v60")
                    if b % 2 == 1:
                        p.put(base + r, PERC, KEYS["clap"], INS["kit"], "v40")
            if hats:
                for r in range(0, bar, 2):
                    p.put(base + r, HAT, KEYS["hat"], INS["kit"],
                          "v44" if r % 4 == 0 else "v30")
                p.put(base + 14, HAT, KEYS["open"], INS["kit"], "v40")
            if shaker:
                for r in range(1, bar, 2):
                    p.put(base + r, PERC, KEYS["shaker"], INS["kit"], "v22")
            if bass:
                for r, off, v in ((0, 0, 64), (3, 0, 50), (6, 12, 56),
                                  (8, 0, 62), (10, 7, 52), (12, 0, 60),
                                  (14, 12, 54)):
                    p.put(base + r, BASS, root + off, INS["bass"], f"v{v}")
            if pad:
                for ch, tone in zip((PAD1, PAD2, PAD3), tones):
                    p.put(base, ch, tone + 12, INS["pad"], "v40")
            if harp:
                arp = [tones[0] + 12, tones[1] + 12, tones[2] + 12,
                       tones[0] + 24, tones[2] + 12, tones[1] + 12,
                       tones[0] + 12, tones[1] + 24]
                for i, r in enumerate(range(0, bar, 2)):
                    p.put(base + r, HARP, arp[i % len(arp)], INS["harp"],
                          f"v{44 if i % 2 == 0 else 34}")
            if lead:
                # chord tones on the beat, so the held pad never sits a
                # second from the line
                motif = ([(0, 4), (3, 2), (6, 0), (8, 4), (12, 2)] if b % 2 == 0
                         else [(0, 2), (3, 4), (6, 7), (8, 4), (11, 2),
                               (14, 0)])
                for r, d in motif:
                    p.put(base + r, LEAD, C.scale_note(ROOT, MODE, d + degree)
                          + 12, INS["lead"], "v50")
            if bell and b % 2 == 0:
                p.put(base, BELL, tones[0] + 36, INS["bell"], "v36")
            if stab and b in (1, 3):
                for r in (6, 14):
                    for ch, tone in zip((PAD1, PAD2, PAD3), tones):
                        p.put(base + r, ch, tone + 12, INS["stab"], "v40")
        if riser:
            p.put(rows - 32, FX, "A-3", INS["riser"], "v44")
        if impact:
            p.put(0, FX, "A-1", INS["impact"], "v60")
        if crash:
            p.put(0, PERC, KEYS["crash"], INS["kit"], "v34")
        if fill:
            for r, key in ((56, "tom_high"), (58, "tom_high"), (60, "tom_low"),
                           (61, "tom_low"), (62, "tom_low"), (63, "snare")):
                p.put(r, SNARE, KEYS[key], INS["kit"], "v56")
        return p

    patterns = [
        pattern("intro", pad=True, bell=True, riser=True),
        pattern("verse1", kit=True, hats=True, bass=True, harp=True, pad=True,
                impact=True),
        pattern("verse2", kit=True, hats=True, snare=True, bass=True,
                harp=True, pad=True, lead=True, shaker=True, fill=True),
        pattern("chorus", kit=True, hats=True, snare=True, bass=True,
                harp=True, pad=True, lead=True, bell=True, stab=True,
                crash=True, impact=True),
        pattern("break", pad=True, harp=True, bell=True, riser=True),
        pattern("chorus2", kit=True, hats=True, snare=True, bass=True,
                harp=True, pad=True, lead=True, bell=True, stab=True,
                crash=True, shaker=True, fill=True),
        pattern("outro", pad=True, bell=True, harp=True, impact=True),
    ]
    head = C.header("Forge Lab", 112, CHANNELS, [
        "author schism-chipgen forge", "mv 60", "sep 96",
        "msg Synthetic demo: every sound is a forge= line (the starter bank),",
        "msg every note is generated from a mode and a progression.",
        "inst 1 name=Tight_Kit forge=kit:tight_kit",
        "inst 2 name=Punch_Bass forge=punch_bass",
        "inst 3 name=Strings forge=strings oct=2-6",
        "inst 4 name=Saw_Lead forge=saw_lead oct=2-6",
        "inst 5 name=Harp forge=harp oct=3-7",
        "inst 6 name=Bell forge=bell oct=4-7",
        "inst 7 name=Stab forge=stab oct=2-6",
        "inst 8 name=Riser forge=riser",
        "inst 9 name=Impact forge=impact",
        "chan 1 pan=32", "chan 2 pan=30", "chan 3 pan=40", "chan 4 pan=24",
        "chan 5 pan=32", "chan 6 pan=14", "chan 7 pan=32", "chan 8 pan=50",
        "chan 9 pan=36", "chan 10 pan=22", "chan 11 pan=46", "chan 12 pan=28",
        "chan 13 pan=32",
    ])
    order = "order " + " ".join(p.name for p in patterns) + "\n"
    return head + "\n" + "\n".join(p.text() for p in patterns) + "\n" + order


def build_one(path):
    from schism import it_write, notation, render, verify
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
    text = open(path, encoding="utf-8").read()
    line = f"mv {module.mix_volume}"
    new = re.sub(r"^mv \d+[ \t]*$", line, text, count=1, flags=re.M)
    if new != text:
        open(path, "w", encoding="utf-8").write(new)


def main(argv):
    os.makedirs(DEMOS, exist_ok=True)
    path = os.path.join(DEMOS, "forge_lab.sch")
    text = build()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"wrote {path} ({len(text) / 1024:.0f} KB)")
    if "--build" in argv:
        build_one(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
