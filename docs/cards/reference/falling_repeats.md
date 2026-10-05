# Card: Repeats of one note, each quieter and each sliding down

**Kind:** pattern technique (S3M). **Checked:** in the module itself, A against B in openmpt and schism; the hypothesis held in both. Nobody listened for this card: it says what the mechanism does, not whether it is good.

## Where it was found

| | |
|---|---|
| module | FearofDark/dancinginthetube.s3m (SHA-256 `fbb5e8c461758d2a…`) |
| address | order 3, pattern 3, row 8, channel 6; the song gets there at 23.997 s (libopenmpt, 44.1 kHz) |
| clock there | speed 6, tempo 125: a tick is 20.0 ms, a row 120.0 ms |
| source | the reference corpus (zip SHA-256 `5b2fbc0cd31a6b07…`); authorship and licence as its catalogue gives them (Mod Archive Distribution license: personal study) |

## What is written there


The channel at the address (libopenmpt's text; instrument and volume in hex):

```text
row   8  A#6 09 .. ...
row   9  ... .. .. E0F
row  10  ... .. .. E0F
row  11  A#6 09v14 ...
row  12  ... .. .. E0F
row  13  ... .. .. E0F
row  14  A#6 09v0A ...
row  15  ... .. .. E0F
```

## What it is for (interpretation)

An echo made by hand: the note is struck again on the same channel two and three rows later at lower volume (v14 = 20, v0A = 10, in hex as libopenmpt prints them), and E0F pulls each strike down while it sounds.

## The A/B

B clears the E0F commands (command and parameter, two bytes a cell).

Bytes B changes in its copy:

| offset | old | new | why |
|---|---|---|---|
| 3440 | 5 | 0 | pattern 3 row 9 channel 6: E0F cleared |
| 3441 | 15 | 0 | pattern 3 row 9 channel 6: E0F cleared |
| 3453 | 5 | 0 | pattern 3 row 10 channel 6: E0F cleared |
| 3454 | 15 | 0 | pattern 3 row 10 channel 6: E0F cleared |
| 3483 | 5 | 0 | pattern 3 row 12 channel 6: E0F cleared |
| 3484 | 15 | 0 | pattern 3 row 12 channel 6: E0F cleared |
| 3490 | 5 | 0 | pattern 3 row 13 channel 6: E0F cleared |
| 3491 | 15 | 0 | pattern 3 row 13 channel 6: E0F cleared |
| 3519 | 5 | 0 | pattern 3 row 15 channel 6: E0F cleared |
| 3520 | 15 | 0 | pattern 3 row 15 channel 6: E0F cleared |

Read for 0.96 s from the address, alone (libopenmpt: every other channel silenced through libopenmpt's interactive interface (S3M: volume 0; others: muted); nothing in the file changes; Schism Tracker: every other channel's disabled bit set in a copy (5 bytes), in A and B alike) and in the mix. The original file's hash is the same after the run: True.

## What was measured

E0F pulls each note down while it sounds: in A the centroid (which follows the pitch for one sample) falls over the slide rows; in B (the E0F cleared) it stays.

- **openmpt** (holds): centroid at the end of each slide over its start: A 0.584, 0.584; B 1.384, 1.384; A - B alone 1.19 dB against A, in the mix -8.06 dB.
- **schism** (holds): centroid at the end of each slide over its start: A 0.6, 0.6; B 1.346, 1.346; A - B alone 1.27 dB against A; Schism's render aligned to libopenmpt's by 2 ms.

Listen: `falling_repeats_alone_A_then_B.mp3` (alone, A then B) and `falling_repeats_mix_A_then_B.mp3` (in the mix), in the training archive's reference folder.

Reproduce: `python3 examples/reference/make.py CORPUS_DIR OUT_DIR falling_repeats`, or one pair by hand: `python3 -m schism corpus ab dancinginthetube.s3m --order 3 --row 8 --seconds 0.96 --channels 6 --change 'cell 3 9 6 effect none' --change 'cell 3 10 6 effect none' --change 'cell 3 12 6 effect none' --change 'cell 3 13 6 effect none' --change 'cell 3 15 6 effect none'`.
