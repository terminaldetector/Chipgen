# Card: A vibrato carried on under volume slides (K after H)

**Kind:** pattern technique (S3M). **Checked:** in the module itself, A against B in openmpt and schism; the hypothesis held in both. Nobody listened for this card: it says what the mechanism does, not whether it is good.

## Where it was found

| | |
|---|---|
| module | FearofDark/dancinginthetube.s3m (SHA-256 `fbb5e8c461758d2a…`) |
| address | order 3, pattern 3, row 8, channel 5; the song gets there at 23.997 s (libopenmpt, 44.1 kHz) |
| clock there | speed 6, tempo 125: a tick is 20.0 ms, a row 120.0 ms |
| source | the reference corpus (zip SHA-256 `5b2fbc0cd31a6b07…`); authorship and licence as its catalogue gives them (Mod Archive Distribution license: personal study) |

## What is written there


The channel at the address (libopenmpt's text; instrument and volume in hex):

```text
row   8  A#5 09 .. ...
row   9  ... .. .. H93
row  10  ... .. .. K02
row  11  ... .. .. K03
row  12  ... .. .. K04
row  13  ... .. .. K04
row  14  G#5 08 .. ...
row  15  G#5 08v11 ...
```

## What it is for (interpretation)

One H93 sets the vibrato (speed 9, depth 3); the K commands after it keep that vibrato going while they fade the note (K is vibrato with the last H's parameters plus a volume slide). The fade and the wobble are one gesture, in one effect column.

## The A/B

B changes each K to D with the same parameter (one byte a cell): the slides stay, the vibrato stops after the H row.

Bytes B changes in its copy:

| offset | old | new | why |
|---|---|---|---|
| 3450 | 11 | 4 | pattern 3 row 10 channel 5: K02 -> D02 |
| 3466 | 11 | 4 | pattern 3 row 11 channel 5: K03 -> D03 |
| 3480 | 11 | 4 | pattern 3 row 12 channel 5: K04 -> D04 |
| 3487 | 11 | 4 | pattern 3 row 13 channel 5: K04 -> D04 |

Read for 0.96 s from the address, alone (libopenmpt: every other channel silenced through libopenmpt's interactive interface (S3M: volume 0; others: muted); nothing in the file changes; Schism Tracker: every other channel's disabled bit set in a copy (5 bytes), in A and B alike) and in the mix. The original file's hash is the same after the run: True.

## What was measured

K is H's vibrato carried on under a volume slide: over the K rows A's pitch wobbles; B (K -> D: the slides kept, the vibrato not) holds still. The H row itself is the same in both.

- **openmpt** (holds): over the K rows the pitch spreads 27.9 cents in A (range 81.7), 3.1 in B (range 16.2); A - B alone -0.69 dB against A, in the mix -8.75 dB.
- **schism** (holds): over the K rows the pitch spreads 28.6 cents in A (range 83.1), 2.9 in B (range 15.5); A - B alone -0.86 dB against A; Schism's render aligned to libopenmpt's by 2 ms.

Listen: `vibrato_memory_under_slides_alone_A_then_B.mp3` (alone, A then B) and `vibrato_memory_under_slides_mix_A_then_B.mp3` (in the mix), in the training archive's reference folder.

Reproduce: `python3 examples/reference/make.py CORPUS_DIR OUT_DIR vibrato_memory_under_slides`, or one pair by hand: `python3 -m schism corpus ab dancinginthetube.s3m --order 3 --row 8 --seconds 0.96 --channels 5 --change 'cell 3 10 5 effect D' --change 'cell 3 11 5 effect D' --change 'cell 3 12 5 effect D' --change 'cell 3 13 5 effect D'`.
