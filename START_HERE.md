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
| `vol fm0 100` | channel volume (FM 0–127, PSG 0–15, `vol dac 55` for the drums) |
| `pan fm1 L` | `L` / `R` / `C` / `off`; `pan fm1 C 2 3` adds AMS/PMS |
| `lfo on 4` | global LFO, rate 0–7 (`lfo off` to stop) |
| `op fm0 4 tl 12` | write one operator field mid-note — see **Live FM** |
| `op opl0 2 wave 2` | the same on an OPL2 channel — see **Live OPL2** |
| `alg fm1 4 6` | change the algorithm, and feedback if given |
| `alg opl0 1 5` | OPL2: `0` FM / `1` additive, plus feedback |
| `pitch fm1 -12` | detune the channel in cents |
| `ch3 special` | give channel 3's four operators separate pitches — see **Channel 3 special** |
| `ch3op 1 A-5` | pitch one of those operators |
| `nes duty nes0 1` | NES pulse waveform — see **The NES** |
| `nes sweep nes0 3 2 up` | the NES's hardware pitch slide (`off` to stop) |
| `cols fm0 fm1 psg0` | which columns the rows below carry |
| `loop` | mark the VGM loop point |
| `pattern verse` | start a named block of rows — see **Form** |
| `order verse main*3 break` | play the patterns in that sequence |
| `mark <label>` | name a section boundary — costs one line, makes `--profile` (below) report by name |
| `chord A-3 min fm2 fm3 fm4` | spread a chord over those channels in one line instead of aligning three columns by hand; `chord off fm2 fm3 fm4` releases them |
| `arp fm1 0 3 7` | tracker arpeggio — that channel's pitch cycles through those semitone offsets inside every row; `arp fm1 off` stops |
| `title` / `author` / `game` / `notes` | metadata written into the .vgm |
| `end` | stop here |

**Cells** — by column:

| column | cells |
|---|---|
| `fm0`–`fm5` | `A-2`, `A#3`, `A-2:100` (velocity 1–127), `===` note off, `...` hold |
| any column | add effects with `/`: `A-2:100/1F0/4A3` — see **Effects** below |
| `psg0`–`psg2` | `A-4`, `A-4:8` (volume 0–15, **0 is loudest**), `===`, `...` |
| `noise` | `w0`–`w3` white, `p0`–`p3` periodic, `===`, `...` |
| `dac` | `kick` `snare` `hat` `hat_open` `tom` `clap` `rim`, or `...`; `kick@D-3` plays it at that pitch, `kick@D-3:0.5` also at half level |
| `nes0`–`nes2` | NES pulse 1, pulse 2, triangle — notes like the FM columns |
| `nes3` | NES noise: a period `0`–`15`, `4m` for the short shift register, `6:80` for velocity |
| `nes4` | NES DMC: a kit sample name, `kick:0.5` for level |

Comments: `;` anywhere, or `#` at the start of a line.

Chord qualities: `maj` `min` `dim` `aug` `sus2` `sus4` `maj6` `min6`
`maj7` `min7` `dom7` `m7b5` `dim7` `add9` `maj9` `min9` `dom9`, plus
shorthands (`m`, `M7`, `7`, `9`, `o7`). Ask for more channels than the
chord has notes and it keeps going up an octave rather than doubling in
unison.

**Form** — a two-minute piece written as one sheet of rows is the wrong
shape for the job. Streets of Rage's title theme is 98 seconds, which at
four rows to the beat is around 650 rows of mostly repetition. Name the
blocks and give an order instead:

```
pattern verse
mark verse
D-2:110  D-5:90   kick
...      ...      hat

pattern break
mark break
D-2:30   ===      ...
...      ...      ...

order verse verse*3 break verse*2
```

Everything before the first `pattern` runs once, as setup. Lines inside a
pattern — rows *and* directives — replay every time the order names it,
which is what lets a `mark` or an instrument change belong to a section.
Anything after the `order` line comes last. A score with no `pattern` in
it behaves exactly as before.

`name*N` repeats, up to 64. Naming a pattern that is not defined is an
error, and so is defining one the order never plays — the symptom
otherwise is a section quietly missing from the render while everything
else works.

Line numbers point at the text you wrote, so an error inside a pattern
used four times still names the one line it is in.

**Your own samples** — `sample` imports a WAV into the kit, and a cell can
ask for a pitch:

```
sample bass808 kits/808.wav C-2     ; C-2 is the pitch the file sounds at
cols dac
bass808@C-2
bass808@G-2
kick@D-3                             ; the built-in kit repitches too
```

Paths are relative to where the render runs, not to the score. Stereo is
mixed down and the file is requantised to 8-bit, because that is what
register 0x2A takes.

One hardware limit worth knowing, because it is invisible otherwise: **the
DAC accepts new bytes at about 15,980 Hz and no faster**. Measured, not
from a datasheet — feed a 220 Hz tone in at rising rates and it reads
220.0 at 11 kHz, 219.7 at 16 kHz, then 159.4 at 22 kHz and 79.7 at 44 kHz,
every failure landing on the same effective 15,980. Above the ceiling the
chip drops bytes, so a sample keeps its **length** and only loses its
**pitch**, which is why it reads as broken repitching rather than as a
limit. chipgen therefore pitches a sample up by thinning its data, not by
feeding faster, and stores imports at a rate the chip can play. Accuracy
across four octaves is within 6 cents, worst at the top where the
thinning gets coarse.

**Live FM** — a patch selected once is a preset. `op` writes one operator
field between two rows, which is how a Genesis driver shapes a note while
it sounds:

```
inst fm0 deep_bass
C-3
...
op fm0 1 tl 10      ; modulator louder: the note brightens here
...
op fm0 1 mul 7      ; and higher, which brightens it further
```

Fields: `tl ar d1r d2r sl rr dt mul ks am ssg` (`dr`/`sr` also accepted for
`d1r`/`d2r`). The operator is 1–4 in the ordinary block-diagram numbering —
the register interleave is handled for you.

Two things to know. The value is absolute, and **the next note-on reloads
the patch over it** — that is the hardware, and the reason real drivers
rewrite these every tick. And two fields share a register (`dt`/`mul`,
`ks`/`ar`, `am`/`d1r`, `sl`/`rr`), so writing one preserves the other
rather than zeroing it.

What this is for, measured: Streets of Rage's title theme changes Total
Level 1,657 times against 6,256 key-ons, and reshapes the decay rates
another 1,300 times. It writes the algorithm register 343 times and
changes its value 15 — so reach for `op`, not `alg`, when a part sounds
static.

**Channel 3 special** — the FM side of this chip has no noise generator,
so a bell or a gong has to be built out of pitches that are not in a
harmonic series. Register 0x27 lets channel 3 do exactly that: each of its
four operators takes its own frequency instead of all four tracking the
note.

The thing to understand first, because it is what makes the mode look
broken: **the supplementary register sets an operator's base frequency,
not a partial you will hear.** Three things stand between the two, and all
three are part of the patch you selected:

1. `mul` multiplies it. `bell_pluck`'s operator 3 runs at `mul 7`, so a
   G-6 written there does not appear at 1568 Hz at all — it sounds at
   10,976 Hz. Measured swing between `mul 7` and `mul 1` at the written
   pitch: over 80 dB.
2. The algorithm decides whether the operator is a carrier or a modulator.
   A modulator's frequency colours its carrier instead of sounding, so the
   pitch you wrote is nowhere in the spectrum.
3. A carrier at `tl 127` is muted. `sub_bass` is algorithm 7 — all four
   operators parallel — with three of them at 127, which is how it gets a
   pure tone. Pitch those three in special mode and nothing happens.

One trap to know if you inspect a patch to work out its multipliers: in
`instruments.py` an `FMInstrument`'s `operators` list is in **register**
order — op1, op3, op2, op4 — the same interleave the chip uses everywhere.
So the second entry in the list is operator *three*. The `op` and `ch3op`
directives both take ordinary block-diagram numbering and undo that for
you; it is only reading the Python that will mislead you.

So `ch3 special` on an arbitrary patch gives an inharmonic smear rather
than the cluster you wrote. To hear the four pitches as written, put the
channel on algorithm 7 (four parallel carriers), set every `mul` to 1, and
give each operator an audible level — which is what `alg` and `op` are
for:

```
inst fm2 sub_bass            ; fm2 IS channel 3
alg fm2 7                    ; four parallel carriers
op fm2 1 mul 1
op fm2 2 mul 1
op fm2 3 mul 1
op fm2 4 mul 1
op fm2 1 tl 8                ; and four audible levels, 6 apart
op fm2 2 tl 14
op fm2 3 tl 20
op fm2 4 tl 26
ch3 special
ch3op 1 A-5
ch3op 2 D#6                  ; a tritone above — deliberately inharmonic
ch3op 3 G-6
cols fm2
A-4                          ; the note-on still keys the channel
...
```

Measured on exactly that score: partials at 880.00, 1244.51, 1567.98 and
440.00 Hz — the three written pitches plus the cell's own note — at 0.0,
−4.6, −9.1 and −14.1 dB relative. That is the TL ladder written above
(6 steps × 0.75 dB = 4.5 dB per rung), so the levels are the patch's, not
an addressing artefact.

Operators are 1–4 in block-diagram numbering. Operator 4 has no
supplementary register of its own and follows the channel's note, which is
also what all four do when the mode is off — so the cell's pitch is never
ignored, it just stops applying to the operators you have pitched
yourself.

Two more things worth knowing, both measured. `ch3 special` affects
channel 3 and nothing else — it is one bit in one register on one channel,
not a global mode, and the other five channels carry on. And **the
operators you pitch keep those pitches across note-ons**: the
supplementary registers are not part of a patch, so a new note re-keys the
cluster rather than replacing it. On the score above the second note
measures identical to the first within 0.1 dB at all four partials, and a
register trace shows `$A8`–`$AE` written once at setup and never again,
while `$A2/$A6` — operator 4, i.e. the channel — is rewritten on every
note-on. Writing `ch3 normal` is what clears it.

`ch3 csm` is the third setting: it also keys the channel on and off from
timer A, which is a speech trick. It is accepted and does nothing useful
without a timer running.

The supplementary registers are `$A9/$AD`, `$AA/$AE`, `$A8/$AC` for
operators 1, 2, 3 — note that **`$A8/$AC` is operator three**, not two.
That was established here by experiment rather than taken from a table:
four operators on algorithm 7 at levels 9 dB apart, every supplementary
pair pointed at a different pitch, then the level measured at each pitch
names the operator sitting there. These registers ascend in the same
op1, op3, op2 order the operator offsets do, which is why a plausible
guess lands on the wrong operator.

**Live OPL2** — `op` takes an OPL column too, same directive rather than
a second name. The operator is **1 (modulator) or 2 (carrier)**: this chip
has two, not four, and its whole algorithm space is the one bit `alg
opl0 0|1` sets — `0` is FM (modulator into carrier), `1` is additive
(both heard). Fields: `tl ar dr sl rr ksl ksr mul am vib eg wave`.

```
inst opl0 opl_organ
cols opl0
A-4:110
...
op opl0 2 tl 30          ; and this is where the trap is, below
op opl0 1 wave 2
```

Two things measured here, both of which make an `op` write look like it
did nothing.

**Which operator to attenuate depends on the connection bit.** On an
additive patch — and `opl_organ` is one — both operators reach the
output, so quietening one leaves the other at full level and the sum
barely moves: measured, `op opl0 2 tl 40` on it gives **−1.7 dB**, the
modulator alone −4.7, and **both together −19.1**. In FM mode (`alg
opl0 0`) the carrier is the only thing heard and the same single write
gives −17.4 dB. So on an additive patch, move both.

**`wave` is the field with no YM2612 equivalent, and two of its four
values shift the octave.** The shapes are `0` sine, `1` half-sine
(negative half clamped), `2` absolute sine (rectified), `3` pulse-sine.
Rectifying doubles the frequency, so waves 2 and 3 put their loudest
partial on the *second* harmonic — measured at A-4, the fundamental is
90 dB down on wave 2 and the note sounds an octave up. A melody that
changes waveform mid-phrase changes octave with it, and nothing errors.
Wave 0 is a pure sine (h2 at −94 dB); wave 1 keeps the fundamental and
brings the even harmonics in at −7 dB.

As on the YM2612, values are absolute and the next `inst` reloads the
patch over them.

Rhythm mode — the OPL2's five percussion voices in register `0xBD` — is
still not implemented, and `python/opl2.py` says so at the top. It needs
the percussion voices actually emulated (they key individual operator
slots on channels 6–8 and draw on the phase generators for the noise
components), which is a different size of job from the rest of this; a
half-right one would sound wrong rather than error. Use the `dac` kit for
drums meanwhile.

**The NES** — the same notation drives an RP2A03. Five columns:
`nes0` and `nes1` are the pulse channels, `nes2` the triangle, `nes3` the
noise and `nes4` the DMC. Aliases exist if you prefer words: `pulse1`,
`pu1`, `sq1` for `nes0`, and `tri`, `nnoise`, `dmc` likewise.

```
bpm 150
lpb 4
cols nes0 nes1 nes2 nes3 nes4
nes duty nes0 1
A-4:110/456  E-5:80   A-2   6:90    kick
...          ...      ...   ...     ...
C-5          G-5:80   C-3   4m:70   hat
```

Everything the Genesis columns do works here — the effect column,
patterns and an order, live register writes, samples. Four things are
specific to this chip, and all four were found by measurement because all
four fail silently.

**The pulse channels are muted below about 110 Hz unless you know one
trick.** The sweep unit silences a channel whenever its *target* period
would pass `$7FF`, and it does that whether or not the sweep is enabled —
with a shift count of zero the target is twice the period, so anything
below period 1023 goes quiet. Measured with the sweep register left at
zero: A-1, C-2, E-2 and G-2 all render **0.0000 RMS**, exact silence,
while A-2 at period 1016 plays fine. Setting the negate bit makes the
target negative instead and the whole octave comes back at full accuracy
— A-1 lands at 55.00 Hz within 0.1 cents. chipgen writes that bit at
init, which is what real NES drivers do, so you never see this. It is
here because a `down` sweep re-arms the mute, and then a bass line
vanishes with no error.

**The ranges are hard floors, and past them a note sounds sharp rather
than low.** The pulses reach **A-1 (55 Hz)**, the triangle **A-0
(27.5 Hz)** — an octave lower, because it divides by 32 where they divide
by 16. Below the floor the 11-bit timer clamps: a written C-1 on a pulse
measures **+888 cents**, which is the same note an octave and a fifth up.
Accuracy is within 4 cents to C-6 and degrades to 12 cents at A-6 as the
timer runs out, the same way the PSG's does. Put the bass on `nes2`.

**The triangle has no volume control at all.** Not a coarse one — none.
Its output measures bit-identical at velocity 8 and velocity 127, so a
velocity on `nes2` is ignored and `7xy`/`Axy` on it are **refused** rather
than accepted and dropped. Pitch effects work on it normally. For a
swelling line use a pulse channel; to shape the triangle, gate it with
note-offs.

**Duty 3 is duty 1 inverted.** `nes duty` takes 0 (12.5%), 1 (25%),
2 (50%) and 3 (75%), but 75% and 25% are one waveform and its inverse:
they measure the same harmonic series and differ only in phase. Two pulse
channels on 1 and 3 give you one timbre twice. Duty 2 is the square — its
even harmonics cancel, measured at −40.9 dB against −5.6 for the others,
which is why it reads as hollow next to them.

`nes sweep nes0 PERIOD SHIFT up|down` is the hardware's own pitch slide,
free in CPU terms but coarse: period and shift are 0–7, and `up`/`down`
is the pitch, not the register (they run opposite ways — the negate bit
raises the pitch). Measured with period 1, shift 3 over half a second:
`up` takes A-4 up 2,716 cents, `down` down 2,815. `nes sweep nes0 off`
returns to the safe default.

`nes4` plays the same sample kit the Genesis DAC does, through `$4011`,
which is a plain 7-bit DAC — so one bit coarser than the Genesis path and
otherwise the same. Effects on it are volume-only, like `dac`.

A NES score exports a .vgm carrying the APU (clock at header offset 0x84,
command 0xB4 per write) and `vgm_player.py` replays it: every voice
round-trips above 0.997 correlation against its own render, which is the
same fidelity the Genesis path gets.

**Effects** — a cell may carry them after the note, separated by `/`:

| code | effect |
|---|---|
| `1xx` / `2xx` | pitch slide up / down, 8 cents per second per unit |
| `3xx` | portamento to the written note |
| `4xy` | vibrato: speed `x`+1 Hz, depth `y` × 8 cents |
| `7xy` | tremolo: speed `x`+1 Hz, depth `y` |
| `8xx` | pan (FM only): `00` left, `80` centre, `FF` right |
| `Axy` | volume slide, (`x`−`y`) × 16 per second |

```
A-2:100/1F0      note at velocity 100, sliding up
.../4A3          vibrato on a note already held
===/A0C          note off with a fade
G-4/3A0/4C4      slide to this note, then vibrato
```

`455` is 6 Hz at 40 cents, which is where the corpus sits — its median
vibrato is 34 cents at 6 Hz across 4,839 measured spans. Start there and
adjust; anything past depth `9` reads as a bend, not a vibrato.

**Which columns take which effects.** All of them take the effect
column — `fm0`–`fm5`, `psg0`–`psg2`, `opl0`–`opl8`, `noise` and `dac` —
but two of them have no pitch to bend:

| column | pitch effects (`1` `2` `3` `4`) | volume effects (`7` `A`) | pan (`8`) |
|---|---|---|---|
| `fm0`–`fm5` | yes | yes | yes |
| `opl0`–`opl8` | yes | yes | no |
| `psg0`–`psg2` | yes | yes | no |
| `noise` | **refused** | yes | no |
| `dac` | **refused** | yes | no |

`noise` and `dac` refuse the pitch codes rather than accept them and do
nothing: the PSG noise rate is four discrete settings, not a continuum,
and the DAC's pitch is its feed rate, which is already against the
15,980 Hz ceiling. For a pitched noise sweep, bend `psg2` and use
periodic noise at rate 3 — the noise channel tracks channel 3's tone
there.

Two measured limits on `dac` effects, because they are smaller than they
look. The effect clock is 60 Hz and the longest built-in sample is 260 ms,
so a slide gets about fifteen ticks: `A0F`, the steepest, reaches roughly
−2 dB by the end of a `tom`. Tremolo fares better because it does not
need to travel — `75A` measures a 7.4 dB swing against the same sample
untouched. And once a drum's own decay has run down to the last few of its
256 codes, scaling it cannot be represented at all, so the tail flattens
out. Effects on the DAC shape a hit; they do not fade a sustained sound,
because there isn't one.

`0xy` (arpeggio) and `Cxx` (note delay) parse and then refuse: both need
to place events between rows, which the cell layer cannot do yet. Use the
`arp` directive for arpeggios. They error rather than doing nothing
quietly, which is the failure that costs you a take.

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

## When you can hear the drums but not the instruments

Run `--levels`. It renders every voice **alone** and tells you what each
one contributes, which is not recoverable from the finished mix — a mix
is a sum.

```
python3 python/chipgen.py song.trk -o song.wav --levels
```

```
voice      patch             rms      peak    vs loudest  crest   headroom
--------------------------------------------------------------------------
dac                          -32.2    -14.7       +0.0   17.4          —
fm0        hl_02             -43.1    -37.6      -22.9    5.5    24.0 dB
fm1        hl_01             -39.2    -35.3      -20.6    3.9    18.0 dB
```

Two things cause this, and the report names both with their remedy.

**The drums own the master, and calibration does not fix it.** Mastering
normalises **peak**, and a drum is almost all peak. Measured against the
*fully calibrated* built-in bank: the DAC channel peaks 5–6 dB above
every FM voice while sitting about 1 dB **below** them in RMS — its crest
factor is 17.4 dB against 5–8 for an FM voice. That gap comes straight
out of everything else's gain. `vol dac 55` measured them level; `vol dac
32` put the FM 5.7 dB on top.

**An imported bank may be 12–18 dB below the one it was dropped into, and
`vol` cannot reach it.** A game's driver rewrites Total Level every tick,
so a patch snapshotted from a .vgm is at whatever level it happened to be
at, not its concert level. Carrier TL 32 is 18 dB below the built-in
bank's TL 8. And `vol` scales **velocity**, which attenuates *down* from
the patch's own level and never above it — so `vol fm0 110` cannot
recover a single dB of it. The fix is the bank:

```
python3 python/vgm_import.py --recalibrate bank.json
```

That levels an existing bank file in place, which previously had no route
at all: calibration only ran during import, so a bank saved with
`--no-calibrate` was stuck unless you still had the original .vgm. On a
bank at the levels above it recovered 13 and 18 TL steps and took the
spread from 22.9 dB down to 6.7.

The `headroom` column is the one actionable number: how much louder the
patch could still go before its carriers hit Total Level 0. The built-in
patches carry 4.5–9 dB of it **on purpose** — that space is what lets
several voices sum without clipping — so a large number is not by itself
wrong. What the warning actually checks is each patch against the bank's
own reference level, the same measurement calibration makes, because that
is the only test that does not depend on what else is in the score.

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
