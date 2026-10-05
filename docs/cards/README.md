# Cards

A card is one technique found in a corpus, written so that a model can use
it without the module: where it is (file, hash, address, the clock there),
what is written there, what it is for in the music, the A/B that takes away
only that mechanism, what was measured, and how sure the card is. None of
them says whether the technique sounds good; nobody listened for them.

| card | technique | checked |
|---|---|---|
| `echo_in_the_envelope.md` | an echo in an instrument's envelopes, rebuilt on this project's own sound (`examples/echo/`) | both players, rebuilt; and in place, below |

## Checked in the reference corpus's modules (`reference/`)

`python3 examples/reference/make.py CORPUS_DIR OUT_DIR --cards=docs/cards/reference`
wrote these, from a run in both libopenmpt and Schism Tracker; every
hypothesis below held in both. The addresses are the corpus audit's six.
`reference_ab.json` has the readings.

| card | module | what B takes out |
|---|---|---|
| `filter_sweep_bass.md` | Skaven, *Fourth Symmetriad*, instrument 2 | the filter envelope |
| `echo_in_the_envelope.md` | Skaven, *Fourth Symmetriad*, instrument 10 | the echo's repeats (node count 9 -> 3) |
| `filter_carry.md` | Manwe, *New Wind*, instrument 2 | the filter envelope's carry |
| `octave_trill_envelope.md` | Manwe, *New Wind*, instrument 15 | the looped pitch envelope |
| `vibrato_memory_under_slides.md` | FearofDark, *Dancing in the Tube*, channel 5 | K -> D (the vibrato, not the slides) |
| `falling_repeats.md` | FearofDark, *Dancing in the Tube*, channel 6 | E0F |
| `volume_gate.md` | Necros, *Isotoxin*, channel 6 | D0F |
| `attack_variants.md` | Necros, *Isotoxin*, channel 8 | instruments 22 and 23 (all 21) |
| `ten_node_decay.md` | FearofDark, *Rose Thorn*, instrument 18 | the volume envelope |

Four of them corrected what was expected before measuring: the Skaven
filter sweep is heard as a resonant peak coming *down through* the
harmonics (the upper band first rises 6-7 dB, then falls 19-21); carry does not
keep an envelope running across a key-off (both players start it again);
the Isotoxin "gate" is a tremolo of about 2 dB on top of the sample's own
pulse; and a filter envelope switched off but still marked as a filter is
heard differently in the two players (`docs/CORPUS.md`).
