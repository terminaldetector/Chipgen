# Card: A ten-node volume envelope: a note that falls and rises again

**Kind:** instrument technique (IT). **Checked:** in the module itself, A against B in openmpt and schism; the hypothesis held in both. Nobody listened for this card: it says what the mechanism does, not whether it is good.

## Where it was found

| | |
|---|---|
| module | FearofDark/fod_rosethorn.it (SHA-256 `449fa710623c14a1…`) |
| address | order 1, pattern 1, row 0, channel 1; the song gets there at 2.035 s (libopenmpt, 44.1 kHz) |
| clock there | speed 6, tempo 157: a tick is 15.924 ms, a row 95.541 ms |
| source | the reference corpus (zip SHA-256 `5b2fbc0cd31a6b07…`); authorship and licence as its catalogue gives them (Mod Archive Distribution license: personal study) |

## What is written there

instrument 18 "", at tempo 157 (a tick is 15.924 ms):

| envelope | nodes (value@tick) | loop | sustain | carry |
|---|---|---|---|---|
| volume | 64@0 11@4 17@13 6@21 12@33 3@43 8@49 1@60 3@68 0@344 | - | - | no |

NNA cut, fade-out 8, cutoff None, resonance None.

The channel at the address (libopenmpt's text; instrument and volume in hex):

```text
row   0  G-4 12 .. A06
row   1  ... .. .. A06
row   2  ... .. .. A06
row   3  ... .. .. A05
row   4  ... .. .. A06
row   5  ... .. .. A05
row   6  ... .. .. A06
row   7  ... .. .. A05
```

## What it is for (interpretation)

The one enabled envelope among the module's 18 instruments: 64 -> 11 -> 17 -> 6 -> 12 -> 3 -> 8 -> 1 -> 3, then a tail to 0 at tick 344 (5.5 s). One note decays in steps that rise again (a built-in echo), and the long tail is cut by the next note (NNA cut, fade-out 8). The chord is spread over three channels two rows apart, and channel 1 alternates speed 6 and 5 (A06 A05): a swing written into the row lengths.

## The A/B

B switches the volume envelope off (flags 1 -> 0): the sample plays at full level.

Bytes B changes in its copy:

| offset | old | new | why |
|---|---|---|---|
| 10310 | 1 | 0 | instrument 18 volume envelope: flags 0x01 -> 0x00 |

Read for 1.6 s from the address, alone (libopenmpt: every other channel silenced through libopenmpt's interactive interface (S3M: volume 0; others: muted); nothing in the file changes; Schism Tracker: every other channel's disabled bit set in a copy (24 bytes), in A and B alike) and in the mix. The original file's hash is the same after the run: True.

## What was measured

The ten-node envelope makes one note fall and rise again (towards ticks 13, 33, 49 and 68) before its long tail: after the attack (the first 150 ms, where the sample's own onset rises too) A's level rises 3 dB or more at least three separate times; B (the envelope off) not once.

- **openmpt** (holds): A rises again at 440, 730, 1000 ms (3.0, 6.3, 6.4 dB), B 0 times; A - B alone 16.12 dB against A, in the mix 12.67 dB.
- **schism** (holds): A rises again at 430, 720, 990 ms (3.1, 6.7, 7.2 dB), B 0 times; A - B alone 16.56 dB against A; Schism's render aligned to libopenmpt's by -1 ms.

Listen: `ten_node_decay_alone_A_then_B.mp3` (alone, A then B) and `ten_node_decay_mix_A_then_B.mp3` (in the mix), in the training archive's reference folder.

Reproduce: `python3 examples/reference/make.py CORPUS_DIR OUT_DIR ten_node_decay`, or one pair by hand: `python3 -m schism corpus ab fod_rosethorn.it --order 1 --row 0 --seconds 1.6 --channels 1 --change 'inst 18 venv off'`.
