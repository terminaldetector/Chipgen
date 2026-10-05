# Card: One pitch struck with three different instruments

**Kind:** pattern technique (S3M). **Checked:** in the module itself, A against B in openmpt and schism; the hypothesis held in both. Nobody listened for this card: it says what the mechanism does, not whether it is good.

## Where it was found

| | |
|---|---|
| module | Necros/isotoxin.s3m (SHA-256 `a666b76286bbd062…`) |
| address | order 44, pattern 52, row 0, channel 8; the song gets there at 173.589 s (libopenmpt, 44.1 kHz) |
| clock there | speed 3, tempo 125: a tick is 20.0 ms, a row 60.0 ms |
| source | the reference corpus (zip SHA-256 `5b2fbc0cd31a6b07…`); authorship and licence as its catalogue gives them (Mod Archive Distribution license: personal study) |

## What is written there

The cells at the address are not copied here (this repository holds no one else's music). The training archive has them: its episode for Necros/isotoxin.s3m, order 44, row 0 (`episodes/INDEX.jsonl`), with the state the song is in when they start.

## What it is for (interpretation)

The same G#6 every two or four rows, with instruments 21, 22 and 23 in turn: three samples for one hit, so the repeats do not sound identical (22 and 23 are 3.7 to 5 dB louder than 21 and differ in brightness).

## The A/B

B sets every strike's instrument to 21 (one byte a cell).

Bytes B changes in its copy:

| offset | old | new | why |
|---|---|---|---|
| 33152 | 22 | 21 | pattern 52 row 4 channel 8: instrument 22 -> 21 |
| 33251 | 23 | 21 | pattern 52 row 12 channel 8: instrument 23 -> 21 |
| 33290 | 22 | 21 | pattern 52 row 14 channel 8: instrument 22 -> 21 |

Read for 0.96 s from the address, alone (libopenmpt: every other channel silenced through libopenmpt's interactive interface (S3M: volume 0; others: muted); nothing in the file changes; Schism Tracker: every other channel's disabled bit set in a copy (14 bytes), in A and B alike) and in the mix. The original file's hash is the same after the run: True.

## What was measured

Three instruments on one pitch: A's strikes differ in level or brightness by instrument; B (all instrument 21) strikes alike.

- **openmpt** (holds): strike levels span 5.07 dB in A, 0.03 dB in B; centroids span 855.6 Hz in A, 103.9 Hz in B; A - B alone -0.2 dB against A, in the mix -19.32 dB.
- **schism** (holds): strike levels span 5.61 dB in A, 0.29 dB in B; centroids span 859.0 Hz in A, 47.1 Hz in B; A - B alone -0.35 dB against A; Schism's render aligned to libopenmpt's by 2 ms.

Listen: `attack_variants_alone_A_then_B.mp3` (alone, A then B) and `attack_variants_mix_A_then_B.mp3` (in the mix), in the training archive's reference folder.

Reproduce: `python3 examples/reference/make.py CORPUS_DIR OUT_DIR attack_variants`, or one pair by hand: `python3 -m schism corpus ab isotoxin.s3m --order 44 --row 0 --seconds 0.96 --channels 8 --change 'cell 52 4 8 instrument 21' --change 'cell 52 12 8 instrument 21' --change 'cell 52 14 8 instrument 21'`.
