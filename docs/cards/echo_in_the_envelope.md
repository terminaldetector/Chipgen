# Card: an echo written into the instrument's envelopes

**Kind:** instrument technique (IT). **Confidence:** high that the mechanism
does what this card says (measured in two players, below); none about taste
(nobody listened for this card).

## Where it was found

| | |
|---|---|
| module | Skaven, *Fourth Symmetriad* (`skaven-fourth_symmetriad.it`) |
| where | instrument 10, "Sine.softecho"; played e.g. in pattern 20, row 0, channel 11 (order 22), as the reference corpus audit gives it |
| how it was read | from the module's instrument header (the corpus audit's `corpus_facts.json`); the module itself is not in this repository, so the episode with its entry state has not been re-read here |
| provenance | the reference corpus `schism_reference_corpus_28_tracks.zip`, SHA-256 `5b2fbc0cd31a6b07489e7c0d23c71cfc42da7ad1542b6e0c4015bb0c1629dd6d` (as the audit records it); authorship as the corpus catalogue gives it |

## What is written there (native, as read)

| envelope | nodes (value@tick) |
|---|---|
| volume | 64@0 13@5 0@15 · 30@16 7@21 0@31 · 15@32 5@34 0@44 |
| pan | 0@0 0@15 · 13@16 13@31 · −13@32 −12@59 |
| filter | 32@0 4@15 · 24@16 3@31 · 16@32 2@45 (filter flag on, cutoff 94) |
| NNA | note off |

One sample (sample 2), no sustain loop, three strikes in the volume envelope.

## What it is for (interpretation)

An echo that belongs to the instrument: the note, then two quieter repeats
16 ticks apart, thrown right and then left, each one darker. It needs no
delay effect, no second channel and no longer sample; the pattern holds only
the note. With NNA "note off" a new note sends the old one to the
background where its repeats play on, so the echoes of one note sound under
the next — the composer's note spacing decides how they overlap.

## The A/B (this project's numbers, not Skaven's)

`examples/echo/` rebuilds the technique on a saw with its own values
(repeats 12 ticks apart, volume 64/34/22, pan ±16, filter 44/36/30 of a
cutoff of 100) and its own melody:

| pair | what differs | files |
|---|---|---|
| `echo_on_note` / `echo_off_note` | the volume envelope's repeats only | `ab_note_echo_then_dry.mp3` |
| `echo_on_motif` / `echo_off_motif` | the same, in a line | `ab_motif_echo_then_dry.mp3` |
| `echo_on_motif` / `echo_nna_cut_motif` | the NNA only | `ab_motif_nna_off_then_cut.mp3` |
| `etude_echo` / `etude_dry` | the same envelope change, with a bass, a kit and a pad | `etude_*.mp3`, `etude_echo.schism.mp3` |

`python3 examples/echo/make.py OUT` writes them and `report.json`.

## What was measured

One note, C-5, tempo 125 (a tick is 20 ms), libopenmpt:

| strike | at | level | rise over the trough before it | right − left | brightness (power centroid / f0) |
|---|---|---|---|---|---|
| the note | 12 ms | −20.7 dB | — | 0.0 dB | 1.10 |
| repeat 1 | 249 ms | −26.3 dB | 47.3 dB | +9.5 dB | 1.02 |
| repeat 2 | 498 ms | −31.4 dB | 46.9 dB | −9.5 dB | 1.01 |

The dry version has no repeats (its "repeat 1" is −106 dB, a rise of 0.3 dB)
and the same first strike to the hundredth of a dB (the envelope falls to 0
before each repeat, which is why the rises are so large). Schism Tracker
plays the same note within 0.05 / 0.09 / 0.21 dB of libopenmpt per strike
and with the same balance. With the notes 24 ticks apart, the previous note's
second repeat (thrown left) lands on each next note: the onset reads −0.3 to
−0.7 dB right-minus-left with NNA note off and exactly 0.0 with NNA cut — the
NNA alone decides whether the echo lives under the next note.

What the measurement does not say: whether the line is better with the echo.
That is the A/B files' job.
