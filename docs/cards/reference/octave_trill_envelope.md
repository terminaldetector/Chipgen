# Card: An octave trill the instrument plays by itself

**Kind:** instrument technique (IT). **Checked:** in the module itself, A against B in openmpt and schism; the hypothesis held in both. Nobody listened for this card: it says what the mechanism does, not whether it is good.

## Where it was found

| | |
|---|---|
| module | Manwe/new_wind_in_syworld.it (SHA-256 `a376f29ad52cef73…`) |
| address | order 0, pattern 5, row 0, channel 9; the song gets there at 0.0 s (libopenmpt, 44.1 kHz) |
| clock there | speed 3, tempo 120: a tick is 20.833 ms, a row 62.5 ms |
| source | the reference corpus (zip SHA-256 `5b2fbc0cd31a6b07…`); authorship and licence as its catalogue gives them (Mod Archive Distribution license: personal study) |

## What is written there

instrument 15 "Sin", at tempo 120 (a tick is 20.833 ms):

| envelope | nodes (value@tick) | loop | sustain | carry |
|---|---|---|---|---|
| volume | 64@0 59@6 40@14 24@24 10@39 4@54 0@79 | - | [0, 0] | no |
| pitch | 0@0 0@2 24@3 24@5 0@6 0@8 24@9 24@11 0@12 0@14 24@15 24@17 0@18 | [0, 4] | - | no |

NNA note off, fade-out 0, cutoff None, resonance None.

The cells at the address are not copied here (this repository holds no one else's music). The training archive has them: its episode for Manwe/new_wind_in_syworld.it, order 0, row 0 (`episodes/INDEX.jsonl`), with the state the song is in when they start.

## What it is for (interpretation)

The pattern holds one note; the instrument's looped pitch envelope throws it up an octave and back every 6 ticks (125 ms here), while the volume envelope lets it fade. An arpeggio that costs no effect column.

## The A/B

B switches the pitch envelope off (flags 3 -> 2: the loop bit stays, the envelope does not run).

Bytes B changes in its copy:

| offset | old | new | why |
|---|---|---|---|
| 8915 | 3 | 2 | instrument 15 pitch envelope: flags 0x03 -> 0x02 |

Read for 1.0 s from the address, alone (libopenmpt: every other channel silenced through libopenmpt's interactive interface (S3M: volume 0; others: muted); nothing in the file changes; Schism Tracker: every other channel's disabled bit set in a copy (21 bytes), in A and B alike) and in the mix. The original file's hash is the same after the run: True.

## What was measured

The looped pitch envelope throws the note up an octave and back every 6 ticks: A's pitch track spends a good part of the note an octave up; B (the envelope off) stays on the note.

- **openmpt** (holds): A spends 25% of its voiced 10 ms steps an octave up, B 0%; A - B alone 1.94 dB against A, in the mix -9.69 dB.
- **schism** (holds): A spends 25% of its voiced 10 ms steps an octave up, B 0%; A - B alone 1.91 dB against A; Schism's render aligned to libopenmpt's by 0 ms.

Listen: `octave_trill_envelope_alone_A_then_B.mp3` (alone, A then B) and `octave_trill_envelope_mix_A_then_B.mp3` (in the mix), in the training archive's reference folder.

Reproduce: `python3 examples/reference/make.py CORPUS_DIR OUT_DIR octave_trill_envelope`, or one pair by hand: `python3 -m schism corpus ab new_wind_in_syworld.it --order 0 --row 0 --seconds 1.0 --channels 9 --change 'inst 15 ienv off'`.
