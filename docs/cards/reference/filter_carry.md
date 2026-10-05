# Card: A filter envelope that carries across held notes

**Kind:** instrument technique (IT). **Checked:** in the module itself, A against B in openmpt and schism; the hypothesis held in both. Nobody listened for this card: it says what the mechanism does, not whether it is good.

## Where it was found

| | |
|---|---|
| module | Manwe/new_wind_in_syworld.it (SHA-256 `a376f29ad52cef73…`) |
| address | order 1, pattern 6, row 0, channel 5; the song gets there at 7.99 s (libopenmpt, 44.1 kHz) |
| clock there | speed 3, tempo 120: a tick is 20.833 ms, a row 62.5 ms |
| source | the reference corpus (zip SHA-256 `5b2fbc0cd31a6b07…`); authorship and licence as its catalogue gives them (Mod Archive Distribution license: personal study) |

## What is written there

instrument 2 "Distortion", at tempo 120 (a tick is 20.833 ms):

| envelope | nodes (value@tick) | loop | sustain | carry |
|---|---|---|---|---|
| volume | 64@0 0@1 | - | [0, 0] | no |
| filter | 17@0 59@9 32@19 (file values, -32..32: -15@0 27@9 0@19) | - | - | yes |

NNA cut, fade-out 0, cutoff 80, resonance 121.

The channel at the address (libopenmpt's text; instrument and volume in hex):

```text
row   0  C-5 02 .. S86
row   2  C-6 02 .. M34
row   3  === .. .. ...
row   6  C-4 02 .. ...
row   8  === .. .. ...
row  12  F-4 02 .. ...
row  18  G-4 02 .. ...
row  24  G-5 02 .. ...
row  25  === .. .. ...
row  28  B-4 02 .. ...
row  30  C-5 02 .. ...
row  32  === .. .. ...
row  34  C-6 02 .. ...
row  35  === .. .. ...
row  38  C-4 02 .. ...
row  40  === .. .. ...
row  44  F-4 02 .. ...
row  50  G-4 02 .. ...
row  56  G-5 02 .. ...
row  57  === .. .. ...
row  60  B-4 02 .. ...
row  62  C-5 02 .. ...
```

## What it is for (interpretation)

The filter sweep marks the start of a phrase, not every note: after a key-off the next note opens the filter again from dark (about 30 dB of upper band in 200 ms); a note that follows a held note keeps the filter where the last one left it. Measured rule, both players: carry continues the envelope only while the previous note of the instrument is still held; a key-off or a note cut starts it again.

## The A/B

B clears the carry bit (flags 137 -> 129): every note sweeps.

Bytes B changes in its copy:

| offset | old | new | why |
|---|---|---|---|
| 1713 | 137 | 129 | instrument 2 filter envelope: flags 0x89 -> 0x81 |

Read for 4.0 s from the address, alone (libopenmpt: every other channel silenced through libopenmpt's interactive interface (S3M: volume 0; others: muted); nothing in the file changes; Schism Tracker: every other channel's disabled bit set in a copy (21 bytes), in A and B alike) and in the mix. The original file's hash is the same after the run: True.

## What was measured

With carry the filter envelope is not started again by a note that follows a held note of the same instrument: that note stays as the last one left the filter (no sweep), while a note after a key-off starts it again (the sweep: about 30 dB from dark to open by 200 ms). With carry off (B) every note sweeps, and the notes after a key-off are the same in A and B.

- **openmpt** (holds): upper-band sweep in the first 240 ms — row 12 after a key-off: A +29.3 dB, B +29.3 dB; row 18 after a note: A +1.2 dB, B +29.1 dB; row 44 after a key-off: A +28.4 dB, B +28.4 dB; row 50 after a note: A +0.4 dB, B +28.3 dB; A - B alone -2.45 dB against A, in the mix -13.08 dB.
- **schism** (holds): upper-band sweep in the first 240 ms — row 12 after a key-off: A +30.2 dB, B +30.2 dB; row 18 after a note: A +0.9 dB, B +30.3 dB; row 44 after a key-off: A +29.2 dB, B +29.2 dB; row 50 after a note: A -0.3 dB, B +29.2 dB; A - B alone -2.41 dB against A; Schism's render aligned to libopenmpt's by 1 ms.

Listen: `filter_carry_alone_A_then_B.mp3` (alone, A then B) and `filter_carry_mix_A_then_B.mp3` (in the mix), in the training archive's reference folder.

Reproduce: `python3 examples/reference/make.py CORPUS_DIR OUT_DIR filter_carry`, or one pair by hand: `python3 -m schism corpus ab new_wind_in_syworld.it --order 1 --row 0 --seconds 4.0 --channels 5 --change 'inst 2 fenv carry off'`.
