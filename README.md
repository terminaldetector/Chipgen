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

**Status: 0.3.** A tested core, four synthetic demo tracks, and the
instrument forge (`docs/FORGE.md`), which makes instruments from a
description of how they should sound, stacks them by role instead of
averaging them, lifts a voice from the NES through the Mega Drive to Schism,
and rewrites a whole module for another rung. Corpora and the model-facing
briefs come next (`docs/ROADMAP.md`).

## Try it

    python3 -m schism check demos/first_light.sch       # compile; list every problem
    python3 -m schism build demos/first_light.sch --mp3 first_light.mp3
    python3 tests/run_tests.py                          # the suite, about four minutes
    python3 -m schism forge survey                      # the forge: every archetype, asked against heard
    python3 -m schism forge layer clap@click snare@body --out mine.bank.json   # a stack, not an average
    python3 -m schism forge lift-voice organ --to schism --demo organ.sch --mp3 organ.mp3   # NES -> Mega Drive -> Schism
    python3 -m schism forge rearrange demos/first_light.sch --to schism --mp3 fl.mp3          # the same notes, other sounds
    python3 -m schism build demos/first_light.sch --strict --preset schism   # --strict fails on a silent note; --preset nes|sega holds it to a console

Python 3.10+ and nothing else is needed to write modules (tested on 3.10,
3.11, 3.12 and 3.13). To *hear* them, install any of:

- `libopenmpt` (`apt install libopenmpt0`) — the checker and renderer, and
  the forge's ear: a sound it makes fresh is rendered and measured with it
  (the starter bank's instruments, as they are, need nothing);
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
- **Instruments made to a description.** `inst 2 forge=punch_bass`,
  `forge=bell dna=brightness:0.8`, `forge=kit:tight_kit`: eleven dials
  (brightness, attack, body, decay, metallicity, detune, motion, bass weight,
  articulation, noise, punch), each defined by a measurement of a note
  libopenmpt renders, are compiled into samples and envelopes, rendered, read
  back and corrected until the sound measures what was asked, then trimmed to
  one loudness. On top: a search (seed, mutate, probe, validate, score,
  retain), mixing instruments, drum kits whose drums leave room for each
  other, and an analysis of a whole module (who masks whom, what to pan, what
  to change) that can repair it. Families: tones, struck and plucked
  sounds, drums, effects, stacks of those, four-operator FM voices.
  `docs/FORGE.md`.
- **Stacks, chords, stereo, backwards.** `forge layer` splices PCM by role
  (the first 30 ms of a clap over the body of a snare; a kick's knock over a
  bass whose loop stays whole), where a blend of dials keeps neither parent;
  `chord=min9`, `stereo=6` and `rev=on` on any forged sound make a chord on
  one key, a stereo pair a few cents apart, a reverse cymbal.
- **A voice up the ladder.** `forge lift-voice` pushes a voice through three
  drivers: the NES (`{duty, noise_mode, arp_intervals}`), the Mega Drive
  (a YM2612 voice in register units, or a DAC drum; the operator numbers go in
  the sidecar) and Schism (octave zones, loops, a stereo pair, drive, cabinet,
  chorus on the organ). The duty-cycle buzz is gone by the last rung; the organ
  has a key click and the snare a crack and a body.
- **A module rewritten.** `forge rearrange` is the second pass over a `.it`
  or `.sch`: other voices for each part, a high bass dropped into range with a
  sub, an arpeggio split into a pad and an arp, an echo channel, a fill from
  the keys the kit has; the notes stay. `docs/FORGE.md`.
- **Export.** `build` fits its own mix volume to a peak (0.89), prints a
  kit's key map, writes `SONG.it.json` (instruments, samples, key maps, loops,
  operators), can fail on a silent note (`--strict`) and hold a module to a
  console's limits (`--preset nes|sega|schism`).

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
| `forge_lab` | 1:00 | 13 | A minor, every sound a `forge=` line from the starter bank: drum kit, punch bass, strings, saw lead, harp, bell, stab, riser, impact |

Each has its `.sch` (the source), `.it` (open it in Schism Tracker) and
`.mp3` (rendered by libopenmpt 0.7.3, encoded by LAME at 192 kbps). The
`.sch` is the truth; `python3 tools/make_demos.py --build` regenerates
the other two, setting each module's mix volume so its render peaks near 0.89
(`python3 tools/make_forge_demo.py --build` does the same for `forge_lab`).

## Layout

    schism/        the package: model, it_write, it_read, synth, recipes,
                   notation, openmpt, schismtracker, verify, levels, render
    schism/forge/  the instrument forge: dials, families, probe, closed loop,
                   search, banks, mixing, layers and PCM edits, FM voices,
                   the NES -> Mega Drive -> Schism ladder, kits, arrangement
                   analysis and rearranging, CLI, banks/starter.bank.json
    docs/          NOTATION.md (the language), FORGE.md (the forge),
                   COVERAGE.md (what is checked), ROADMAP.md
    demos/         four synthetic tracks
    tests/         run_tests.py; format, synth, notation, audio, demos,
                   engines, docs, export, forge_core, forge, layers, ladder,
                   rearrange
    tools/         make_demos.py, make_forge_demo.py, make_docs.py,
                   compare_engines.py
    corpus/        a note on the corpora that are planned

## Limits worth knowing

Schism Tracker plays samples 1–235 and loads at most 240 patterns (Impulse
Tracker 2.14 itself: 99 and 200). A pitched instrument costs one sample per
octave, so keep `oct=` narrow; the compiler refuses a score past Schism's
limits and warns past Impulse Tracker's. Compressed samples can be read as
headers only, so most `.it` files from the internet cannot yet be ingested.
Stereo samples are written and read (planar, as both players have it).
