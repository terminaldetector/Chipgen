# chipgen — start here

You are looking at a chiptune engine. It emulates the two sound chips of a
Sega Mega Drive / Genesis — the **YM2612** (4-operator FM, 6 channels) and
the **SN76489** (Sega PSG, 3 square channels + noise) — and turns a written
score into audio through them. Not a synthesiser imitating that sound: the
same registers, the same protocol, the same timings.

If you are a language model that has just been handed this archive: this
page is everything you need to *write a score*. Read it, run the two
commands, compose.

If you are going to touch the chip below the score layer — designing
instrument patches, writing register values, building a bank — read
`bridge/CORE.md` first. It is the hardware, measured, including about a
dozen places where the YM2612 contradicts what is generally true of FM
synthesis. Every one of them fails silently: the render succeeds and the
output is wrong.

---

## 1. Set it up (once, ~2 seconds)

```bash
python3 bridge/bootstrap.py
```

It finds a C compiler and builds the chip cores, or falls back to
pure-Python cores if there is no compiler. It then renders a test pattern
and tells you exactly what it found. **No network, no pip install, no
config.** numpy and scipy are used if present and not needed if absent.

If it prints `READY`, everything below works.

## 2. Write a score

Tracker notation: one row per step, one column per voice.

```
title My Track
bpm 150
lpb 4                      ; 4 rows per beat = sixteenth notes
inst fm0 bass
inst fm1 square_lead
inst fm2 strings
cols fm0 fm1 fm2 psg0 noise dac

D-2  ...  D-4  D-5   ...  kick
...  ...  ...  A-5:6 ...  hat
A-2  A-4  ...  F-5   w1   snare
...  ...  ...  A-5:6 ...  hat
```

## 3. Render it

```bash
python3 python/chipgen.py song.trk -o song.wav --vgm song.vgm
```

You get `song.wav` (playable anywhere) and `song.vgm` (a register log that
plays in any VGM player and **imports into DefleMask and Furnace**, so a
human can open what you wrote in a real tracker and keep editing).

From Python instead:

```python
import sys; sys.path.insert(0, "python")
import chipgen
chipgen.compose(open("song.trk").read(), wav="song.wav", vgm="song.vgm")
```

---

## The notation, completely

**Directives** — anywhere, one per line:

| directive | meaning |
|---|---|
| `bpm 150` | tempo |
| `lpb 4` | rows per beat (4 = 16ths, 8 = 32nds) |
| `inst fm0 bass` | assign a patch to an FM channel |
| `vol fm0 100` | channel volume (FM 0–127, PSG 0–15) |
| `pan fm1 L` | `L` / `R` / `C` / `off`; `pan fm1 C 2 3` adds AMS/PMS |
| `lfo on 4` | global LFO, rate 0–7 (`lfo off` to stop) |
| `pitch fm1 -12` | detune the channel in cents |
| `cols fm0 fm1 psg0` | which columns the rows below carry |
| `loop` | mark the VGM loop point |
| `mark <label>` | name a section boundary — costs one line, makes `--profile` (below) report by name |
| `chord A-3 min fm2 fm3 fm4` | spread a chord over those channels in one line instead of aligning three columns by hand; `chord off fm2 fm3 fm4` releases them |
| `arp fm1 0 3 7` | tracker arpeggio — that channel's pitch cycles through those semitone offsets inside every row; `arp fm1 off` stops |
| `title` / `author` / `game` / `notes` | metadata written into the .vgm |
| `end` | stop here |

**Cells** — by column:

| column | cells |
|---|---|
| `fm0`–`fm5` | `A-2`, `A#3`, `A-2:100` (velocity 1–127), `===` note off, `...` hold |
| `psg0`–`psg2` | `A-4`, `A-4:8` (volume 0–15, **0 is loudest**), `===`, `...` |
| `noise` | `w0`–`w3` white, `p0`–`p3` periodic, `===`, `...` |
| `dac` | `kick` `snare` `hat` `hat_open` `tom` `clap` `rim`, or `...` |

Comments: `;` anywhere, or `#` at the start of a line.

Chord qualities: `maj` `min` `dim` `aug` `sus2` `sus4` `maj6` `min6`
`maj7` `min7` `dom7` `m7b5` `dim7` `add9` `maj9` `min9` `dom9`, plus
shorthands (`m`, `M7`, `7`, `9`, `o7`). Ask for more channels than the
chord has notes and it keeps going up an octave rather than doubling in
unison.

**Instruments**

| role | patches |
|---|---|
| bass | `sub_bass` (pure sine) · `deep_bass` (round, long body) · `slap_bass` (sharp snap, clean body) · `techno_bass` (buzzy, driving) · `bass` (bright throughout) |
| lead | `saw_lead` · `square_lead` · `distorted_lead` |
| pluck / stab | `hard_pluck` · `bell_pluck` · `pluck_guitar` · `fm_stab` · `metal_stab` · `orch_hit` |
| keys / pad | `e_piano` · `organ` · `brass` · `strings` · `jazz_chord_pad` |

The five basses are ordered above by how much harmonic content they carry —
pick by how much room the rest of your mix needs, not at random. Run
`python3 python/chipgen.py --info` for a one-line description of each.
They are levelled to within about two decibels of each other, so swapping
one for another changes the timbre and not the balance of your arrangement.

**Want more?** Two ways to get hundreds more patches, both levelled
against the built-in bank on import so they drop straight in:

```bash
# 1. any Genesis VGM — replays the register stream, snapshots each channel
#    at key-on, dedupes, ranks by how often the composer used each patch
python3 python/vgm_import.py song.vgm -o bank.json

# 2. Furnace / DefleMask / TFM instrument files (.dmp .tfi .vgi).
#    Furnace ships a library of ~600 YM2612 patches sorted by category.
python3 python/furnace_import.py path/to/instruments/OPN --filter bass -o bank.json

python3 python/chipgen.py score.trk --bank bank.json -o out.wav
```

Names carry their category (`bass_fat_bass_1`, `keys_e_piano`), so
`--filter bass` picks one folder out of a whole library. Calibration
renders every patch, so use `--filter` or `--limit` rather than importing
six hundred at once.

---

## Before you compose: what this music actually does

`corpus/STUDY.md` is 79 real Mega Drive tracks — Gunstar Heroes, Streets
of Rage, Thunder Force IV and seven more — transcribed into this exact
notation and then measured. Read it before writing, not after: it is the
idiom, not a review checklist. About 700 tokens for the statistics, 8,500
with the excerpts attached.

The headlines, all counted rather than asserted:

- FM0 is the bass on every track in the set. FM5 appears on 37% of them,
  because the DAC takes channel 6 for drums.
- A third of note-to-note moves repeat the pitch; most of the rest step by
  a tone or less. Leaps are 19%, and they are mostly octaves.
- One to three FM voices sound at a time. Five or six is 7% of rows.
- Median FM velocity is 21 of 127 — notes are not all struck at full.
- Vibrato runs about 34 cents at 6 Hz. It colours a note, it does not
  bend it.

`bridge/LEARNING.md` explains why the digest exists rather than the
archive: the full corpus is ~1.5 million tokens, and thirty-one arbitrary
tracks is a worse sample than all 235 summarised.

## Things that will bite you if nobody says them

- **PSG volume is backwards.** `0` is loudest, `15` is silent. It is an
  attenuator, not a fader. This is the chip's fault, not ours.
- **One FM channel plays one note.** A three-note chord needs three
  channels. There are six.
- **The DAC steals channel 6.** Using the `dac` column silently takes over
  `fm5` while a sample plays — same trade the real hardware makes. Do not
  plan a six-voice arrangement and a drum track.
- **PSG runs out of resolution up high.** Its tone register is 10 bits, so
  above roughly C6 neighbouring semitones start landing on the same pitch.
  Put lead lines on FM and let the PSG do arpeggios.
- **Give notes somewhere to go.** `...` means *hold*, not *rest*. A note
  sounds until `===` or the next note in that column.
- **Do not restart the noise on every hat.** Writing the PSG's noise
  register resets its shift register, so a hat gated on and off sixteen
  times a bar replays the identical waveform and turns into a buzz. The
  `w1` cell does the right thing already; only reach for a restart when
  you want a short blip to sound identical every time.
- **Name your sections with `mark` and check `--profile` after rendering.**
  A section that should drop to near-silence and doesn't (a pattern
  generator's "inactive" branch holding instead of releasing, so a note
  rings on from the section before) is invisible to the automatic warnings
  above — they only ever see whole-track totals, and a channel busy
  elsewhere in the piece clears them easily even while one specific
  section is stuck. `chipgen.py score.trk -o out.wav --profile` prints
  RMS/peak per `mark`-delimited section by name (or per bar if you skip
  `mark` and pass `--beats-per-bar`); a breakdown that reads 0.26 next to
  a drop's 0.27 is not a breakdown. This is the single most useful check
  for anything with real dynamic structure — run it, don't just trust
  that zero warnings means the arrangement is right.
- **Let the noise channel and the DAC rest.** There is one shared noise
  voice and one 8-bit sample channel for the whole piece. Gating either
  one on and never releasing it for the length of a track — a constant
  hiss bed, or a sample chain with no gaps between hits — reads as a wall
  of noise, not rhythm, however musical the rest of the arrangement is.
  `compose()` runs a quick check on this before rendering and will warn
  you (`python3 python/chipgen.py --info` shows nothing missing, but a
  render's own output will say e.g. "the noise channel is one continuous
  95%-of-the-track span") — read those warnings, they are naming a real
  problem in what you wrote, not a formality.

## If you want JSON instead

The tracker grid is a view of a flat event list, and you can emit that
list directly — one JSON object per event, in order. `chipgen.compose()`
accepts it, and repairs the usual slips (an out-of-range channel, a
lowercase note name, a missing terminator) instead of refusing the take.

```json
[{"type": "FMInstrumentSelect", "channel": 0, "instrument": "bass"},
 {"type": "FMNoteOn", "channel": 0, "note": "A", "octave": 2},
 {"type": "Wait", "ticks": 24},
 {"type": "FMNoteOff", "channel": 0},
 {"type": "End"}]
```

Every event type, field, default and valid range is in
`bridge/manifest.json`, or freshly generated by
`python3 python/chipgen.py --info`.

## Where things are

```
bridge/bootstrap.py     run this first
bridge/manifest.json    the whole vocabulary, machine-readable
python/chipgen.py       one-call API + CLI
python/tracker.py       the notation, with its grammar in the docstring
python/events.py        the event vocabulary
python/instruments.py   the FM patch bank
python/vgm_import.py    pull instruments out of any Genesis VGM
python/calibrate_bank.py  re-level the bank after adding a patch
core/                   the C chip emulation (Nuked-OPN2 + Sega PSG)
README.md               how and why the whole thing works
```

## Two knobs worth knowing

`--chip ym2612` (default) emulates the discrete Model 1 chip, whose DAC
ladder makes silence slightly gritty and lets a hard-panned channel bleed
into the other side. `--chip ym3438` is the later integrated ASIC: clean
muting, clean silence. Both are real hardware.

`--dc-block` is on by default and simply centres the mix. Turn it off with
nothing — it has no off switch on the CLI because there is no musical
reason to want the offset; use the Python API if you are comparing against
an unfiltered capture.
