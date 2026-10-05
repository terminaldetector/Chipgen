# What the format can do, and how an agent reaches it

One row per capability: what Impulse Tracker's format (as Schism Tracker and
libopenmpt play it) does, what this project does with it, the command, the
test that holds it, and what is missing. The chip engines are Chipgen's
(`bridge/CAPABILITIES.md` on its branch).

Schism is a sample player. A sample made here is synthesised, not recorded,
and it is still a sample; an FM voice rendered to one is an IT instrument
with an FM origin, not a YM2612 channel, and a square wave in a 16-channel
module is not an NES. `build --preset nes|sega` holds a module to a
console's channel count and drum channel; it does not make it that console.

| format | this project | agent command | test | gap |
|---|---|---|---|---|
| samples: 8/16 bit, mono or stereo (planar), loop and sustain loop, ping-pong | written and read; stereo checked in both players | `wave=...`, `smp`, forge `stereo=` | test_format, test_audio | IT 2.14/2.15 compressed samples are not unpacked: a module with them is read for its patterns and refused for rewriting, by name, its file untouched |
| volume / pan / pitch-or-filter envelopes, 25 nodes, sustain loop, loop, carry | written from ticks (`venv=`, `carry` among the nodes) or from seconds (forge, with merges reported); read; carry measured in both players | `venv=` `penv=` `ienv=` `fenv=`; `explain` shows them node by node in ms at the module's tempo, with what carry does | test_audio, test_programs (merges, clock moves, carry) | pitch and filter share a slot (the format's limit, refused by name) |
| NNA, DCT, DCA, fade-out | written | `nna=` `dct=` `dca=` `fade=`; `explain` says what the next note and a key-off do | test_audio, test_programs | — |
| resonant filter (cutoff, resonance), Zxx | written | `cutoff=` `res=`, `fenv=`, Zxx | test_audio | the filter is an IT/OpenMPT feature; other formats do not have it |
| sample auto-vibrato (speed, depth, sweep, wave) | written | `vib=` | test_audio | — |
| pattern effects A-Z, the volume column, note off / cut / fade | written, read, the effect memory of each | the cell notation (`C-5 01 v64 H44`) | test_format, test_audio, test_corpus (libopenmpt's effect numbers against the letters) | legato is not an instrument setting in IT: it is Gxx on the next note; one effect column a cell (plus the volume column), so two effects on one note need the volume column, a second row or a second channel |
| order list, jumps, breaks, pattern loops | written and read | `order`, Bxx/Cxx/SBx | test_corpus (what plays against what is listed) | — |
| render rate | libopenmpt at any rate; Schism Tracker's disk writer at the rate of its `[Diskwriter]` section, checked in the WAV it writes | `--engine schism` | test_engines (22 050 and 48 000 Hz in tune and the same length) | — |
| reading other people's modules | IT for editing; IT, XM, S3M, MOD through libopenmpt for analysis | `corpus index / trace / episode` | test_corpus | no XM/S3M/MOD editor import; no FTM/Furnace macro reader |
| one mechanism of someone else's module, A against B | a copy changed in a few bytes (IT instrument envelopes, NNA, fade-out, filter; S3M cells), played from the song's start, cut where the song plays the address, alone and in the mix, in both players | `corpus ab`; `examples/reference/make.py` (nine cards) | test_ablate | IT pattern cells (refused: packed with a memory of the last values), XM and MOD cells |
| a note read phase by phase | `forge/phases.py` (attack on a 1 ms grid, body, held, release, power brightness, pitch, loop seam) | the examples and the forge's own probes | test_programs | no bounded improve loop for IT instruments yet (Chipgen has one for the YM2612) |
| an instrument explained | `forge/program.py` | `explain SCORE [N]`, `check --json` | test_programs | — |

## Measured for this table

- Schism Tracker's `--diskwrite` wrote 44 100 Hz whatever `[Audio]
  sample_rate` asked; the rate is `[Diskwriter] rate`. Before the fix a
  48 000 Hz request came back mislabelled: 1.5 semitones sharp and 8 % short.
- The writer used to write a compressed sample as an empty one; it now
  refuses the module, and `forge rearrange`/`analyse` refuse a packed `.it`
  before doing anything.
- libopenmpt's effect numbers for IT A-Z, by writing each letter and reading
  it back (A 16, B 12, C 14, D 11, E 3, F 2, G 4, H 5, I 18, J 1, K 7, L 6,
  M 21, N 22, O 10, P 29, Q 15, R 8, S 20, T 17, U 26, V 23, W 24, X 9, Y 27,
  Z 31).
- The echo card's mechanism plays the same in Schism Tracker and libopenmpt,
  within 0.21 dB per strike; in the reference corpus's modules the two
  agree on all nine reference cards' hypotheses.
- Carry continues an envelope only while the previous note of the same
  instrument is held; a key-off or a note cut starts it again (both
  players, 0.2 dB).
- libopenmpt does not play a muted S3M channel's commands (as Scream
  Tracker 3 did not), so muting moves the song; Schism Tracker plays them.
- A filter envelope switched off but still marked "filter" lowers the
  filter in Schism Tracker and not in libopenmpt.
- libopenmpt's clock depends on the rate (whole samples a tick): 0.24 s
  apart at 200 s between 8 and 44.1 kHz at tempo 128.
- libopenmpt prints instrument and volume numbers in hex (`14v40` is
  instrument 20 at volume 64).
