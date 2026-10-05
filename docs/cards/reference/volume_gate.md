# Card: A held note pulsed by alternating volume slides and resets

**Kind:** pattern technique (S3M). **Checked:** in the module itself, A against B in openmpt and schism; the hypothesis held in both. Nobody listened for this card: it says what the mechanism does, not whether it is good.

## Where it was found

| | |
|---|---|
| module | Necros/isotoxin.s3m (SHA-256 `a666b76286bbd062…`) |
| address | order 44, pattern 52, row 0, channel 6; the song gets there at 173.589 s (libopenmpt, 44.1 kHz) |
| clock there | speed 3, tempo 125: a tick is 20.0 ms, a row 60.0 ms |
| source | the reference corpus (zip SHA-256 `5b2fbc0cd31a6b07…`); authorship and licence as its catalogue gives them (Mod Archive Distribution license: personal study) |

## What is written there


The channel at the address (libopenmpt's text; instrument and volume in hex):

```text
row   0  D-6 14 .. S87
row   1  ... .. .. D0F
row   2  ... ..v40 ...
row   3  ... .. .. D0F
row   4  ... ..v40 ...
row   5  ... .. .. D0F
row   6  ... ..v40 ...
row   7  ... .. .. D0F
row   8  ... ..v40 ...
row   9  ... .. .. D0F
row  10  ... ..v40 ...
row  11  ... .. .. D0F
row  12  ... ..v40 ...
row  13  ... .. .. D0F
row  14  ... ..v40 ...
row  15  ... .. .. D0F
```

## What it is for (interpretation)

One D-6 held for the whole pattern; D0F on the odd rows pulls it down (64 to 34 over a row of three ticks) and v40 (64) on the even rows puts it back: a tremolo at the row rate, 120 ms a cycle, on top of the sample's own pulse.

## The A/B

B clears the D0F commands; the v40 resets stay.

Bytes B changes in its copy:

| offset | old | new | why |
|---|---|---|---|
| 33122 | 4 | 0 | pattern 52 row 1 channel 6: D0F cleared |
| 33123 | 15 | 0 | pattern 52 row 1 channel 6: D0F cleared |
| 33138 | 4 | 0 | pattern 52 row 3 channel 6: D0F cleared |
| 33139 | 15 | 0 | pattern 52 row 3 channel 6: D0F cleared |
| 33169 | 4 | 0 | pattern 52 row 5 channel 6: D0F cleared |
| 33170 | 15 | 0 | pattern 52 row 5 channel 6: D0F cleared |
| 33189 | 4 | 0 | pattern 52 row 7 channel 6: D0F cleared |
| 33190 | 15 | 0 | pattern 52 row 7 channel 6: D0F cleared |
| 33210 | 4 | 0 | pattern 52 row 9 channel 6: D0F cleared |
| 33211 | 15 | 0 | pattern 52 row 9 channel 6: D0F cleared |
| 33234 | 4 | 0 | pattern 52 row 11 channel 6: D0F cleared |
| 33235 | 15 | 0 | pattern 52 row 11 channel 6: D0F cleared |
| 33272 | 4 | 0 | pattern 52 row 13 channel 6: D0F cleared |
| 33273 | 15 | 0 | pattern 52 row 13 channel 6: D0F cleared |
| 33302 | 4 | 0 | pattern 52 row 15 channel 6: D0F cleared |
| 33303 | 15 | 0 | pattern 52 row 15 channel 6: D0F cleared |

Read for 0.96 s from the address, alone (libopenmpt: every other channel silenced through libopenmpt's interactive interface (S3M: volume 0; others: muted); nothing in the file changes; Schism Tracker: every other channel's disabled bit set in a copy (14 bytes), in A and B alike) and in the mix. The original file's hash is the same after the run: True.

## What was measured

D0F after each v40 pulls the held note down on the odd rows (64 to 34 over a row of 3 ticks, about -2 dB on the row's average) and v40 puts it back: in A the odd rows are quieter than the even ones by more than they are in B (the D0F cleared), where only the sample's own shape is left.

- **openmpt** (holds): odd rows minus even rows: A -5.7 dB, B -2.99 dB; A - B alone -12.46 dB against A, in the mix -23.17 dB.
- **schism** (holds): odd rows minus even rows: A -7.31 dB, B -3.0 dB; A - B alone -10.31 dB against A; Schism's render aligned to libopenmpt's by 2 ms.

Listen: `volume_gate_alone_A_then_B.mp3` (alone, A then B) and `volume_gate_mix_A_then_B.mp3` (in the mix), in the training archive's reference folder.

Reproduce: `python3 examples/reference/make.py CORPUS_DIR OUT_DIR volume_gate`, or one pair by hand: `python3 -m schism corpus ab isotoxin.s3m --order 44 --row 0 --seconds 0.96 --channels 6 --change 'cell 52 1 6 effect none' --change 'cell 52 3 6 effect none' --change 'cell 52 5 6 effect none' --change 'cell 52 7 6 effect none' --change 'cell 52 9 6 effect none' --change 'cell 52 11 6 effect none' --change 'cell 52 13 6 effect none' --change 'cell 52 15 6 effect none'`.
