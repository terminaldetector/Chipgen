# Card: An echo written into the instrument's envelopes (checked in place)

**Kind:** instrument technique (IT). **Checked:** in the module itself, A against B in openmpt and schism; the hypothesis held in both. Nobody listened for this card: it says what the mechanism does, not whether it is good.

## Where it was found

| | |
|---|---|
| module | Skaven252/skaven-fourth_symmetriad.it (SHA-256 `f2a56492733d1ec6…`) |
| address | order 22, pattern 20, row 0, channel 11; the song gets there at 199.922 s (libopenmpt, 44.1 kHz) |
| clock there | speed 4, tempo 128: a tick is 19.531 ms, a row 78.125 ms |
| source | the reference corpus (zip SHA-256 `5b2fbc0cd31a6b07…`); authorship and licence as its catalogue gives them (Mod Archive Distribution license: personal study) |

## What is written there

instrument 10 "Sine.softecho", at tempo 128 (a tick is 19.531 ms):

| envelope | nodes (value@tick) | loop | sustain | carry |
|---|---|---|---|---|
| volume | 64@0 13@5 0@15 30@16 7@21 0@31 15@32 5@34 0@44 | - | - | no |
| pan | 0@0 0@15 13@16 13@31 -13@32 -12@59 | - | - | no |
| filter | 64@0 36@15 56@16 35@31 48@32 34@45 (file values, -32..32: 32@0 4@15 24@16 3@31 16@32 2@45) | - | - | no |

NNA note off, fade-out 0, cutoff 94, resonance 0.

The cells at the address are not copied here (this repository holds no one else's music). The training archive has them: its episode for Skaven252/skaven-fourth_symmetriad.it, order 22, row 0 (`episodes/INDEX.jsonl`), with the state the song is in when they start.

## What it is for (interpretation)

An echo that belongs to the instrument: three strikes in the volume envelope, the repeats thrown right and then left by the pan envelope and darkened by the filter envelope, NNA note off so a note's repeats sound under the next notes. Here the line runs down in steps of two rows and stops, and its echoes go on after it, alternating sides.

## The A/B

B keeps the volume envelope's first three nodes (64@0 13@5 0@15: the dry strike) and drops the repeats: one byte, the node count 9 -> 3.

Bytes B changes in its copy:

| offset | old | new | why |
|---|---|---|---|
| 7222 | 9 | 3 | instrument 10 volume envelope: keep the first 3 of 9 nodes |

Read for 1.85 s from the address, alone (libopenmpt: every other channel silenced through libopenmpt's interactive interface (S3M: volume 0; others: muted); nothing in the file changes; Schism Tracker: every other channel's disabled bit set in a copy (15 bytes), in A and B alike) and in the mix. The original file's hash is the same after the run: True. Instruments 26, 27, 28, 29, 30, 31… of this module vary their volume or pan at random, so the readings in the mix move a little from run to run (those of the channel alone do not, unless it plays one of them).

## What was measured

The repeats are the envelope's: after the line stops, A goes on striking, right then left of the dry note; B (the envelope cut after its first strike) is silent, and the first strike is the same in both.

- **openmpt** (holds): after the line stops, A strikes 3 more times (+2.9, -5.1, -6.4 dB right of the dry note), B none; tail level A -32.6 dB, B -50.52 dB; first strike -19.7 / -19.7 dB; A - B alone -6.77 dB against A, in the mix -15.31 dB.
- **schism** (holds): after the line stops, A strikes 5 more times (+4.5, +3.4, +3.9, -3.7, -6.4 dB right of the dry note), B none; tail level A -33.44 dB, B -51.56 dB; first strike -18.43 / -18.43 dB; A - B alone -6.78 dB against A; Schism's render aligned to libopenmpt's by 2 ms.

Listen: `echo_in_the_envelope_alone_A_then_B.mp3` (alone, A then B) and `echo_in_the_envelope_mix_A_then_B.mp3` (in the mix), in the training archive's reference folder.

Reproduce: `python3 examples/reference/make.py CORPUS_DIR OUT_DIR echo_in_the_envelope`, or one pair by hand: `python3 -m schism corpus ab skaven-fourth_symmetriad.it --order 22 --row 0 --seconds 1.85 --channels 11 --change 'inst 10 venv nodes 3'`.
