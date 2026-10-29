"""The form of a recording, from its audio alone: a map of its stretches
for comparing an arrangement with our own (the audit of 27 October, P1).

    python3 -I examples/agentic/references/audio_form.py RECORDING \\
        [--bpm 120] [--beats 16] [--out form.json]

The recording is decoded the way a reference is (references._decode: lame
or ffmpeg, AAC inside an MP4 recognised by its signature) and cut into
stretches of `beats` beats. Each stretch is described as a section is in
form.py — chroma, onsets per beat, brightness, level — the stretches are
lettered (A, A', B), and the sharp switches of density, brightness or
level between neighbours are listed. The beat is given, not guessed: a
recording's tempo windows (references._tempo_windows) say where a guess
would be unsure.

This is audio and nothing else. It says nothing about which chips played
or which commands they were sent: for that a VGM or the source is needed.
The recording is read where it is and never copied.
"""

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "python"))

from agentic import form as FM            # noqa: E402
from agentic import references as REF     # noqa: E402
from agentic import render as R           # noqa: E402


def stretches(mono, rate, bpm, beats):
    beat = 60.0 / bpm
    size = int(beats * beat * rate)
    out = []
    for start in range(0, len(mono) - size // 2, size):
        d = FM._audio_slot(mono[start:start + size], rate, beat)
        d["t"] = round(start / rate, 1)
        out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("--bpm", type=float, default=120.0)
    ap.add_argument("--beats", type=int, default=16)
    ap.add_argument("--seconds", type=float, default=600.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "decoded.wav")
        how = REF._decode(a.recording, wav, a.seconds)
        stereo = REF.read_wav(wav)
    mono = [(stereo[i] + stereo[i + 1]) / 2 for i in range(0, len(stereo), 2)]
    parts = stretches(mono, R.RATE, a.bpm, a.beats)
    n = len(parts)
    sim = [[FM._audio_sim(parts[i], parts[j]) for j in range(n)]
           for i in range(n)]
    letters = FM.letters(sim)
    switches = FM.switches(parts)
    out = {"recording": os.path.basename(a.recording), "decoded": how,
           "bpm": a.bpm, "beats_per_stretch": a.beats,
           "letters": " ".join(letters),
           "stretches": [{"t": p["t"], "letter": l,
                          "onsets_per_beat": round(p["per_beat"], 2),
                          "centroid_hz": round(p["centroid"]),
                          "level_db": round(p["level_db"], 1)}
                         for p, l in zip(parts, letters)],
           "switches": [{"t": parts[w["at"]]["t"], "what": w["what"]}
                        for w in switches],
           "says_nothing_about": "chips, registers, commands"}
    print(out["letters"])
    for w in out["switches"]:
        print(f"{w['t']:7.1f} s  " + ", ".join(w["what"]))
    if a.out:
        with open(a.out, "w") as handle:
            json.dump(out, handle, indent=1)


if __name__ == "__main__":
    main()
