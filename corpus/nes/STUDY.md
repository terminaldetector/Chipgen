# NES corpus — what this music does

158 tracks from 12 games, 48626 notes, transcribed from VGM
register logs by `python/nes_transcribe.py` and measured by `python/study.py`.
Read it before writing for the chip, not after. `bridge/NES.md` is the hardware.

Two caveats that are part of the data, not disclaimers about it:

- **Percussion is unreliable.** A drum here is the noise channel re-triggered at
  the same period — the hit is the envelope restarting, not a pitch change — and
  the transcriber emits notes on pitch changes. Noise appears in a minority of
  these tracks when nearly all of them have drums. The pitched channels are what
  this corpus is for.
- **Scores are JSON, not `.trk`.** A `.trk` is something this project can render,
  and an NES score is not one yet.

## Games

- Batman - Return of The Joker (NES) — 13 tracks
- Batman Returns (NES) — 20 tracks
- Blaster Master (NES) — 13 tracks
- Bucky O'Hare (NES) — 18 tracks
- Contra (NES) — 13 tracks
- Final Fantasy (NES) — 18 tracks
- Ghostbusters II (NES) — 3 tracks
- Gradius II (Family Computer) — 10 tracks
- Metal Gear (NES) — 10 tracks
- Ninja Gaiden (NES) — 25 tracks
- RoboCop 3 (NES) — 1 tracks
- Teenage Mutant Ninja Turtles (NES) — 14 tracks

## Measured

```
style profile — 158 tracks

tempo      74 .. 181 BPM, median 100
mode       minor 93, major 65
voices     harmony 127, lead 111, counter 85, bass 78, arp 32, pad 24

harmony    unison 16%, 5 14%, maj 11%, min 10%, sus2 8%, maj7 8%
           90% played as chords, the rest spelled out

progressions repeated within a track and seen in several:
           i - VII - i - VII                x10
           VII - i - VII - i                x8
           i - VI - i - VI                  x6
           i5 - i5 - I7 - I7                x5
           i5 - I7 - I7 - VImaj7            x5

what transfers between tracks (share of all pattern instances):
           rhythm alone            19.8%
           contour alone           26.4%
           the two fused           0.2%

--- bass ---
  rhythm   xxxxxxxxxxxxxxxx   x160   16 tracks   sync 0.75
  rhythm   x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x- x38    12 tracks   sync 0.75
  rhythm   x-x-x-x-x-x-x-x-   x17     4 tracks   sync 0.50
  rhythm   x---x---x---x---x---x---x---x--- x12     4 tracks   sync 0.50
  contour  +-+-+-+-+-+-+-+    x65    14 tracks
  contour  +-+-+-+            x10     4 tracks
  contour  -+-+-+-            x7      3 tracks

--- lead ---
  rhythm   xxxxxxxxxxxxxxxx   x36    13 tracks   sync 0.75
  rhythm   x-x-x-x-x-x-x-x----------------- x18     4 tracks   sync 0.75
  rhythm   xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx x9      4 tracks   sync 0.88
  rhythm   x-x-x-----------   x5      4 tracks   sync 0.33
  contour  -+++               x9      6 tracks
  contour  -++-               x7      6 tracks
  contour  --++               x7      5 tracks

--- harmony ---
  rhythm   xxxxxxxxxxxxxxxx   x66    14 tracks   sync 0.75
  rhythm   x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x- x14     7 tracks   sync 0.75
  rhythm   x-x-x-x-x-x-x-x-   x37     6 tracks   sync 0.50
  rhythm   xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx x20     5 tracks   sync 0.88
  contour  -+-+-+-            x21     5 tracks
  contour  ++--               x10     5 tracks
  contour  -+-+-+             x10     5 tracks

--- arp ---
  rhythm   x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x- x60     8 tracks   sync 0.75
  rhythm   xxxxxxxxxxxxxxxx   x61     7 tracks   sync 0.75
  contour  +-+-+-+-+-+-+-+    x28     7 tracks
  contour  +-+-+-+            x19     4 tracks
  contour  -+-+-+-            x11     3 tracks

--- counter ---
  rhythm   x-x-x-x-x-x-x-x-   x98     9 tracks   sync 0.50
  rhythm   xxxxxxxxxxxxxxxx   x22     8 tracks   sync 0.75
  rhythm   x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x- x15     4 tracks   sync 0.75
  rhythm   xxxx-xxxxxxxxxxx   x6      4 tracks   sync 0.80
  contour  +-+--              x7      4 tracks
  contour  -+--               x6      4 tracks
  contour  +--+               x5      4 tracks

--- pad ---
  contour  -+--               x15     3 tracks
  contour  -++-               x4      3 tracks
```
