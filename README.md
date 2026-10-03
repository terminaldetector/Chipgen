# schism-chipgen

Models write tracker music. A plain-text score goes in; an Impulse Tracker
module (`.it`) comes out — the format [Schism Tracker](https://schismtracker.org),
OpenMPT and every module player open — with every instrument synthesised
from a recipe, and two independent players checking that what was written
is what plays.

It is the sibling of Chipgen, which lets models write chip music through
register-accurate emulation of the Genesis, OPL2 and NES sound chips. Where
Chipgen's models write the chip's registers, here they write what a tracker
shows: notes, instruments, volume column and effects, in rows.

**Status: 0.1.** A tested core and three synthetic demo tracks. Corpora and
the model-facing briefs come next (`docs/ROADMAP.md`).

## Try it

    python3 -m schism check demos/first_light.sch       # compile; list every problem
    python3 -m schism build demos/first_light.sch --mp3 first_light.mp3
    python3 tests/run_tests.py                          # 75 tests, under a minute

Python 3.10+ and nothing else is needed to write modules (tested on 3.10,
3.11, 3.12 and 3.13). To *hear* them, install any of:

- `libopenmpt` (`apt install libopenmpt0`) — the checker and renderer;
- `schism` (`apt install schism`) — the program itself, run headless;
- `lame` (`apt install lame`) or `ffmpeg` — for MP3.

## A score

```sch
title Hello
tempo 120
channels 2
smp thump wave=kick
smp tick  wave=rim
inst 1 name=Lead  wave=square oct=3-6 nna=cut venv=0:64,30:40s,60:40s,90:0 fade=60
inst 2 name=Drums kit=C-2:thump,D-2:tick

pattern verse rows 32
C-5 01 v64 ... | C-2 02 v64 ...
... .. ... ... | ... .. ... ...
E-5 01 v64 ... | D-2 02 v48 ...
... .. ... ... | ... .. ... ...
G-5 01 v64 H44 | C-2 02 v64 ...
... .. ... H00 | ... .. ... ...
=== .. ... ... | D-2 02 v48 ...
rest 25
end

order verse verse
```

A cell is `NOTE INS VOL FX`; a row is the cells of every channel between `|`;
that is the shape every tracker prints, so it is the shape a model has read
the most of. The whole language is in `docs/NOTATION.md`.

## What it does that a plain writer does not

- **Every problem at once.** The compiler returns all the mistakes in a
  score in one pass — line, source text, and the fix — instead of one per
  round.
- **Warnings for silent failures.** A note before any instrument, a key-off
  that cannot end its note, a `gain=1` the player renders as silence, a
  vibrato that never starts: each compiles, plays nothing, and is reported.
- **Sounds with no samples.** Band-limited single-cycle waves (one sample
  per octave, tuned by arithmetic), seamless pads, drums, a plucked string,
  a bell — all synthesised and deterministic.
- **Two outside witnesses.** libopenmpt and Schism Tracker itself read and
  play every module; `docs/COVERAGE.md` says what each confirmed, with the
  numbers, and where the two differ.
- **Who is audible.** `schism/levels.py` solos each channel and reports its
  loudness against the whole, because the usual way to lose an instrument is
  a quiet one, and the render succeeds either way.

## The demos

Synthetic in both senses: every sound is synthesised from a recipe (there is
no recording in this repository), and every note is generated from musical
rules — a mode, a chord progression, a rhythm — rather than transcribed
from a piece.

| demo | length | channels | what it shows |
|---|---|---|---|
| `glass_engine` | 1:38 | 10 | synthwave in A minor: drum kit, filtered bass, detuned pad, pulse lead, pluck, bell, riser |
| `first_light` | 1:04 | 8 | chip-style: pulse, triangle, noise percussion, arpeggios, a laser |
| `pattern_garden` | 1:33 | 10 | Euclidean (Bjorklund) rhythms and seeded random-walk melodies in D dorian: kit, bass, FM electric-piano chords, reed lead, bell, toms |

Each has its `.sch` (the source), `.it` (open it in Schism Tracker) and
`.mp3` (rendered by libopenmpt 0.7.3, encoded by LAME at 192 kbps). The
`.sch` is the truth; `python3 tools/make_demos.py --build` regenerates
the other two, setting each module's mix volume so its render peaks near 0.89.

## Layout

    schism/        the package: model, it_write, it_read, synth, recipes,
                   notation, openmpt, schismtracker, verify, levels, render
    docs/          NOTATION.md (the language), COVERAGE.md (what is checked),
                   ROADMAP.md
    demos/         three synthetic tracks
    tests/         run_tests.py; format, synth, notation, audio, demos,
                   engines, docs
    tools/         make_demos.py, make_docs.py, compare_engines.py
    corpus/        a note on the corpora that are planned

## Limits worth knowing

Schism Tracker plays samples 1–235 and loads at most 240 patterns (Impulse
Tracker 2.14 itself: 99 and 200). A pitched instrument costs one sample per
octave, so keep `oct=` narrow; the compiler refuses a score past Schism's
limits and warns past Impulse Tracker's. Compressed samples can be read as
headers only, so most `.it` files from the internet cannot yet be ingested.
