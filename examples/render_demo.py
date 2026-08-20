"""
render_demo.py — the end-to-end smoke test: rule-based generator ->
event list -> real chip emulation -> WAV + VGM.

Run it from anywhere; paths are resolved relative to this file.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "python"))

import mixer
import vgm
import wavio
from demo_generator import generate_pattern
from sequencer import Sequencer

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")


def main():
    seq = Sequencer(ticks_per_second=192.0, target_rate=44100)
    events = generate_pattern(ticks_per_second=seq.ticks_per_second, bars=4)
    print(f"generated {len(events)} events")

    wav_path = os.path.join(OUTPUT_DIR, "demo.wav")
    vgm_path = os.path.join(OUTPUT_DIR, "demo.vgm")
    tag = vgm.GD3(title="chipgen demo", author="chipgen rule-based generator")

    audio = seq.render(events, vgm_path=vgm_path, gd3=tag)
    audio = mixer.normalize_peak(audio)
    wavio.write(wav_path, audio, seq.target_rate)
    print(f"rendered {wavio.describe(audio, seq.target_rate)}")
    print(f"wrote {wav_path}")
    print(f"wrote {vgm_path}")


if __name__ == "__main__":
    main()
