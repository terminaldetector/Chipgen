# Card: A resonant filter swept by the instrument over a held note

**Kind:** instrument technique (IT). **Checked:** in the module itself, A against B in openmpt and schism; the hypothesis held in both. Nobody listened for this card: it says what the mechanism does, not whether it is good.

## Where it was found

| | |
|---|---|
| module | Skaven252/skaven-fourth_symmetriad.it (SHA-256 `f2a56492733d1ec6…`) |
| address | order 1, pattern 1, row 96, channel 2; the song gets there at 17.492 s (libopenmpt, 44.1 kHz) |
| clock there | speed 4, tempo 128: a tick is 19.531 ms, a row 78.125 ms |
| source | the reference corpus (zip SHA-256 `5b2fbc0cd31a6b07…`); authorship and licence as its catalogue gives them (Mod Archive Distribution license: personal study) |

## What is written there

instrument 2 "ChipBass.sweep", at tempo 128 (a tick is 19.531 ms):

| envelope | nodes (value@tick) | loop | sustain | carry |
|---|---|---|---|---|
| volume | 64@0 11@3 0@13 | - | [0, 0] | no |
| filter | 64@0 47@42 32@100 19@166 12@254 (file values, -32..32: 32@0 15@42 0@100 -13@166 -20@254) | - | - | no |

NNA note off, fade-out 0, cutoff 127, resonance 115.

The channel at the address (libopenmpt's text; instrument and volume in hex):

```text
row  96  D-4 02v28 S88
```

## What it is for (interpretation)

A bass that changes colour while it holds, on one sample: the movement is the instrument's, not the PCM's. Instrument 1 (ChipBass.raw) plays the same sample with no filter; instrument 2 adds a filter envelope that lowers the cutoff over five seconds, with the resonance high (115), so what is heard is a resonant peak travelling down through the harmonics.

## The A/B

B clears the envelope's on bit and its filter mark (flags 0x81 -> 0x00). With the on bit alone cleared the two players disagree: Schism Tracker still lowers the filter, as if the envelope stood at its middle value (a resonant peak near 820 Hz), libopenmpt does not; with both cleared they agree.

Bytes B changes in its copy:

| offset | old | new | why |
|---|---|---|---|
| 2953 | 129 | 0 | instrument 2 filter envelope: flags 0x81 -> 0x00 |

Read for 2.5 s from the address, alone (libopenmpt: every other channel silenced through libopenmpt's interactive interface (S3M: volume 0; others: muted); nothing in the file changes; Schism Tracker: every other channel's disabled bit set in a copy (15 bytes), in A and B alike) and in the mix. The original file's hash is the same after the run: True. Instruments 26, 27, 28, 29, 30, 31… of this module vary their volume or pan at random, so the readings in the mix move a little from run to run (those of the channel alone do not, unless it plays one of them).

## What was measured

The filter envelope moves the instrument's resonant filter over the held note (instrument 1 is the same sample with no filter): in A the upper band (1.2-5 kHz) moves 10 dB or more along the note as the resonant peak comes down through it; in B (the envelope off) every band stays within 1 dB.

- **openmpt** (holds): A: the 1.2-5 kHz band spans 19.3 dB along the note (peak near 851 Hz at 1.8 s, 635 Hz at 2.3 s); B: every band within 0.1 dB; A - B alone -7.67 dB against A, in the mix -13.11 dB.
- **schism** (holds): A: the 1.2-5 kHz band spans 21.1 dB along the note (peak near 851 Hz at 1.8 s, 624 Hz at 2.3 s); B: every band within 0.0 dB; A - B alone -7.56 dB against A; Schism's render aligned to libopenmpt's by 28 ms.

Listen: `filter_sweep_bass_alone_A_then_B.mp3` (alone, A then B) and `filter_sweep_bass_mix_A_then_B.mp3` (in the mix), in the training archive's reference folder.

Reproduce: `python3 examples/reference/make.py CORPUS_DIR OUT_DIR filter_sweep_bass`, or one pair by hand: `python3 -m schism corpus ab skaven-fourth_symmetriad.it --order 1 --row 96 --seconds 2.5 --channels 2 --change 'inst 2 fenv off'`.
