# What is built, and how it was checked

Three witnesses stand behind every claim here, and each claim says which.

| witness | what it is | how it is used |
|---|---|---|
| **the project** | `schism/it_write.py` and `schism/it_read.py`, written from ITTECH.TXT | round trips: what is written is read back cell for cell |
| **libopenmpt 0.7.3** | the engine OpenMPT plays with, loaded through `ctypes` (`schism/openmpt.py`) | an independent reader (`verify.compare`), the duration oracle, and the renderer for the MP3s |
| **Schism Tracker** | the program the project is named for, build of 2024-01-29 (Ubuntu 24.04 package `schism` 2:20240129-1), run headless with `--diskwrite` (`schism/schismtracker.py`) | a second player: every audio measurement in the suite can be re-run against it |

The writer and the reader were written by the same hands from the same
document, so they can agree and both be wrong. That is why the other two
exist. Neither Impulse Tracker itself (DOS, 1995–97) nor Schism's GUI was
run; the headless renderer is Schism's own mixer.

Run it yourself:

    python3 tests/run_tests.py                          # libopenmpt plays
    SCHISM_ENGINE=schism python3 tests/run_tests.py     # Schism Tracker plays
    SCHISM_ENGINE=schism SCHISM_INTERPOLATION=linear python3 tests/run_tests.py
    python3 tools/compare_engines.py                    # the table below

Schism has four interpolation settings (nearest, linear — its default —
spline, and an 8-tap FIR). The audio measurements pass under all four; the
tuning bound is 2.5 cents under `nearest`, which cannot place a note closer,
and 1.5 cents elsewhere. The wrapper plays with `spline` unless told otherwise.

Without libopenmpt or Schism installed the tests that need them skip, and
the rest (the file format, the synthesiser, the compiler, the manual) run
on the Python standard library alone.

## The file format

| part of the format | write | read | checked |
|---|---|---|---|
| header: title, flags, global/mix volume, speed, tempo, separation | yes | yes | round trip; libopenmpt; Schism loads it |
| channel panning, volume, mute | yes | yes | round trip; mute is how `levels.py` solos a channel |
| order list with end marker | yes | yes | libopenmpt; Schism |
| song message | yes | yes | round trip |
| MIDI configuration block (4896 bytes) | yes, if given | read and kept | round trip; no notation for it |
| patterns: packed, 32..200 rows, repeat flags | yes | yes | 12 random 64-row, 9-channel patterns cell for cell, three ways |
| instruments (IT 2.x layout, 554 bytes): NNA, DCT, DCA, fadeout, pitch-pan, global volume, default pan, random swing, cutoff/resonance, note map | yes | yes | round trip; libopenmpt |
| three envelopes (volume, pan, pitch or filter), 25 nodes, loop and sustain loop | yes | yes | round trip; heard: each envelope measured in both players |
| samples: 8- or 16-bit mono signed PCM, loops, ping-pong, sustain loops, C5 speed, auto-vibrato | yes | yes | round trip; heard |
| samples that share data share its bytes | yes | — | size test |
| compressed samples (IT 2.14/2.15) | no | the header only; the data is flagged `compressed` and not unpacked | round trip of the flag |
| stereo samples | no | refused by name | — |
| IT 1.x instrument layout (Cmwt < 0x200) | no | refused by name | — |
| OpenMPT extensions (pattern and channel names, plugins) | no | ignored | — |
| edit history, timestamps | no | skipped | — |

**Limits the writer enforces**, because a module past them opens in one
player and not in another. Measured on Schism Tracker: samples 1–235 play,
the 236th is silent, and a file with 237 never loads (the program sits
waiting); patterns up to 240 load, and 241 stalls it. OpenMPT stops at 240
patterns too. Impulse Tracker 2.14 itself holds 99 samples and 200
patterns, so the compiler *warns* past those and *refuses* past Schism's.
A pitched instrument costs one sample per octave it covers, which is how a
score reaches 235: keep `oct=` narrow.

## What an effect does (measured)

All 26 effect letters are written and read back exactly. The ones below
have also been *heard* — a note is rendered and its pitch, level or timing
measured, and the assertion passes in **both** players:

| effect | measured behaviour |
|---|---|
| `F10` / `E10` | a pitch slide of one semitone per tick, on ticks 1–5 of a six-tick row (about 500 cents a row, ±40) |
| `G10` | tone portamento reaches the target note within 5 cents and stays |
| `J47` | arpeggio: 0, +4, +7 semitones, one step per tick (each step within 12 cents) |
| `H48` | vibrato: about ±50 cents |
| `D04` | volume slide: 4/64 of full volume per tick (five slides a row), so each row is quieter than the last and the note is silent by the fifth |
| `SC3` | cut after 3 ticks: ticks 0–2 sound, ticks 4 onward are silent |
| `SD3` | delay 3 ticks: ticks 0–2 are silent, 3 onward sound |
| `Q01` / `Q02` / `Q03` | retrigger every 1 / 2 / 3 ticks: the sample starts over, window for window; nothing carries into the next row without a new `Qxy` |
| `X00` / `XFF` | hard left / hard right (the far side more than 40 dB down) |
| `V40`, `M20`, `v32` | each halves the amplitude (0.5 ± 0.03) |
| `R48`, `I13` | tremolo swings the level by more than 25%; tremor silences at least six of twelve ticks |
| `Z10` | with IT's default macros, closes the filter |
| `A`, `T`, `B`, `C`, `SEx`, `S6x` | these change the song's length; `Module.seconds()` follows them and agrees with libopenmpt on a table of nine cases, `Cxx` taking its row in plain hex |

Envelopes and instruments, measured the same way:

- a volume envelope runs in ticks and in straight lines;
- a pan envelope runs −32 (left) to +32 (right);
- a pitch envelope counts **half semitones**: 32 is 16 semitones (±10 cents);
- a filter envelope scales the instrument's `cutoff` by value/64 (a
  constant 32 on cutoff 64 sounds like cutoff 32, within 12%; 64 is the
  cutoff as set), so it can close the filter from the cutoff and never
  open it past it; swept from low to 64 it opens the filter more than
  2.5 times and the pitch stays put (±15 cents), in both players. An
  earlier version wrote the envelope with the *carry* flag (bit 3)
  instead of the filter flag (bit 7), so a filter envelope played as a
  pitch sweep in both players; the test that should have noticed
  measured brightness only, which a rising pitch also raises;
- `nna=cont` lets the old note ring while the new one sounds; `nna=cut` does not;
- `~~~` and `===` need `fade=` (or a volume envelope that ends at 0) to end a
  note — without one, the note rings on, in both players;
- instrument `gain=` is applied in steps of two: 0 and 1 are silent, 2 and 3
  are equal, 4 is twice 2, and `gain=128` is sixteen times `gain=8`;
- sample auto-vibrato: each unit of depth is 1.5625 cents (so `vib=…/12/…`
  is ±18.75); *rate* is how fast it fades in, the depth growing by `rate`
  256ths a tick (rate 64 takes 48 ticks to reach depth 12, rate 8 takes 384),
  and **rate 0 never starts it** — the compiler warns.

Tuning: every pitched wave (sine, saw, square, triangle, pulse, fm), the
plucked string and the pad play the note asked for to within 1.3 cents on
24 notes spread over the keyboard, in both players. (An early pluck was
20.7 cents sharp — a delay line that read a tap it had already overwritten;
the test found it and the tuning is now arithmetic, not hope.)

## Two players, one module

The three demos rendered by libopenmpt and by Schism Tracker, compared by
`tools/compare_engines.py` (Schism minus libopenmpt, octave bands from
20 Hz, in dB):

| demo | length (score / Schism / libopenmpt) | peak | 20 | 40 | 80 | 160 | 320 | 640 | 1.3k | 2.6k | 5.1k | 10k |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| First Light | 64.00 / 64.00 / 64.10 s | 0.874 / 0.891 | −0.3 | 0.0 | −0.1 | 0.0 | 0.0 | 0.0 | 0.0 | +0.2 | 0.0 | +0.1 |
| Glass Engine | 97.63 / 97.59 / 97.69 s | 0.895 / 0.887 | −0.2 | 0.0 | −0.2 | −0.3 | 0.0 | +0.1 | +0.2 | +0.1 | 0.0 | −0.1 |
| Pattern Garden | 92.90 / 92.89 / 92.99 s | 0.885 / 0.886 | −0.1 | 0.0 | −0.1 | 0.0 | +0.1 | +0.1 | 0.0 | +0.3 | +0.3 | 0.0 |

- **Length.** Schism stops on the last row, within 0.05% of the arithmetic;
  libopenmpt's render runs about 0.1 s past it. (libopenmpt's own
  `duration` call, which rounds each tick to whole samples at 48 kHz, is up
  to 0.1% shorter than either; the test allows 0.2%.)
- **Level and balance.** Left/right balance agrees within 0.2 dB. A held
  note is 2.9% louder in Schism (a constant), whatever the instrument,
  envelope or volume.
- **Spectrum.** Every octave band agrees within ±0.4 dB, under Schism's
  linear (default), spline and 8-tap interpolation alike.
- **What does not agree**, and so is not a check: sample-by-sample
  subtraction. Channels with `H` vibrato or a long held note drift out of
  phase between the engines (waveform correlation 0.5–0.9 where it is
  0.98–1.00 on a steady channel), because the vibrato LFO and the playback
  increments are rounded differently; the spectra still agree. Level,
  spectrum and pitch are the checks.

## The synthesiser

- Pitched waves are built from their Fourier series, one sample per octave,
  with no harmonic above 19 kHz at the sample's top note, so nothing
  aliases when it is played up. Each cycle is a whole number of frames and
  `c5speed` is that number times C-5, so every note is in tune.
- Pads are long seamless loops (an integer number of cycles per voice, the
  join no bigger than an ordinary step).
- Every one-shot ends at exactly zero through a short raised-cosine fade: a
  sample still sounding when its data ends stops with a step, and a step is
  a click (the kick ended at 7% of full scale, the low pluck at 19%, the
  bell at 16%).
- Everything is deterministic: a seed, never the clock.

## Not built

- Reading compressed (IT 2.14/2.15) samples, so most modules found on the
  internet cannot yet be ingested; stereo samples; IT 1.x instruments.
- XM, S3M and MOD (read or write).
- Notation for MIDI macros, pattern and channel names, the pitch-wheel
  depth, instrument MIDI output.
- A preview mixer in pure Python, for a machine with neither libopenmpt nor
  Schism. Without one, a model on such a machine gets the compiler's
  checks and warnings but cannot measure what it wrote.
- The training corpora (`corpus/README.md`) and the article (`docs/ROADMAP.md`).
- Impulse Tracker itself and the Schism GUI have not been run.
