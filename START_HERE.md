# chipgen — start here

You are looking at a chiptune engine. It emulates the two sound chips of a
Sega Mega Drive / Genesis — the **YM2612** (4-operator FM, 6 channels) and
the **SN76489** (Sega PSG, 3 square channels + noise) — and turns a written
score into audio through them. Not a synthesiser imitating that sound: the
same registers, the same protocol, the same timings.

If you are a language model that has just been handed this archive: this
page is everything you need. Read it, run the two commands, write a score.

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

**Instruments** — `bass`, `sub_bass`, `slap_bass`, `distorted_lead`,
`square_lead`, `bell_pluck`, `e_piano`, `organ`, `brass`, `strings`,
`pluck_guitar`, `jazz_chord_pad`, `orch_hit`, `metal_stab`.
Run `python3 python/chipgen.py --info` for what each one sounds like.
They are levelled to within about a decibel of each other, so swapping one
for another changes the timbre and not the balance of your arrangement.

**Want more?** Any Genesis VGM is an instrument source:

```bash
python3 python/vgm_import.py song.vgm -o bank.json   # read its patches out
python3 python/chipgen.py score.trk --bank bank.json -o out.wav
```

It replays the register stream, snapshots each channel at key-on, dedupes,
ranks by how often the composer actually used each patch, and levels the
result against the built-in bank.

---

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
