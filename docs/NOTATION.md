# The score language (`.sch`)

A score is a plain-text file that describes a tracker module the way a
tracker shows one: a few header lines, the sounds, then patterns of rows.
`python3 -m schism build song.sch` turns it into a `.it` file that opens in
Schism Tracker, OpenMPT and every other module player.

Nothing here is recorded. Every sound is a *recipe* (`wave=saw`,
`wave=kick`, `wave=pluck`) that the compiler synthesises, so a score is
small, readable and complete in itself, and a model can write it without
holding any audio.

The tables below are generated from the code (`tools/make_docs.py`) and
every `sch` example is compiled by the test suite, so what you read is what
the compiler accepts.

## A whole score

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

- One directive per line. `#` or `;` starts a comment (a `#` inside a note
  such as `C#4` is a note, not a comment).
- A **cell** is four fields: `NOTE INS VOL FX`, e.g. `C-5 01 v64 H44`.
  An empty cell is `... .. ... ...`, or just `...`.
- A **row** is one cell per channel, separated by `|`.
- `rest 25` is twenty-five empty rows, so a pattern with little in it is
  not written out line by line.
- Every row of every pattern has the same number of cells; the first row
  fixes it (or `channels N` does).

## Header

| directive | meaning |
|---|---|
| `title TEXT` | the module's name (25 characters are kept) |
| `author TEXT` | written at the top of the song message as `by TEXT` |
| `msg TEXT` | a line of the song message (repeat for more lines) |
| `tempo BPM` | beats per minute, 20..400; the compiler solves it into IT's speed and tempo (see Timing) |
| `rpb N` | rows per beat, 1..32, default 4 |
| `speed N` or `speed auto` | ticks per row; `auto` (default) lets the compiler choose, preferring 6 |
| `channels N` | 1..64; default is the width of the first row |
| `chan N pan=0..64 vol=0..64 mute` | channel N's panning (`pan=surround` too), volume, mute |
| `gv N` | global volume 0..128, default 128 |
| `mv N` | mix volume 0..128, default 48 — the module's own loudness; `tools/make_demos.py` sets it so the render peaks near 0.89 |
| `sep N` | pan separation 0..128, default 128 |
| `flags W...` | `linear` (default) or `amiga` slides; `oldfx`; `gxxlink` |
| `smp NAME wave=...` | define a named sample (see Sounds) |
| `inst N key=value...` | define instrument N, 1..99 |
| `pattern NAME rows N` ... `end` | a pattern of 32..200 rows |
| `order a b*2` | the play order; `b*2` repeats `b` |

## Sounds

An instrument is one line of `key=value` pairs with no spaces around the
`=`. It needs exactly one of:

- `wave=KIND` — synthesise a sound (table below). A *pitched* wave becomes
  one sample per octave of `oct=LOW-HIGH` (default `1-7`), each band-limited
  so it does not alias when played up; the lowest and highest samples are
  stretched to the ends of the keyboard. A *looped* wave is one long loop
  played at every pitch. A *one-shot* plays once, at its natural pitch.
- `sample=NAME` — use a sample defined earlier with `smp NAME wave=...`
  (the same recipes, defined once and reused).
- `kit=KEY:NAME,KEY:NAME,...` — a drum kit: each key plays its own named
  sample at its natural pitch. A key without a sample is silent (the
  compiler warns when a pattern plays one).

`smp NAME wave=KIND [settings]` takes the wave's own settings and `vol=`.

### Waveforms

<!-- generated:waves -->
| `wave=` | stored as | what it is | settings (default, range) |
|---|---|---|---|
| `sine` | pitched | a sine, one cycle per octave sample | — |
| `saw` | pitched | a band-limited saw, one sample per octave | — |
| `square` | pitched | a band-limited square | — |
| `triangle` | pitched | a band-limited triangle | — |
| `pulse` | pitched | a band-limited pulse of any width | `duty=0.25` (0.02..0.5) |
| `fm` | pitched | two-operator FM: sin(p + index*sin(ratio*p)), one sample per octave | `ratio=2` (1..16), `index=3` (0..12), `fb=0` (0..4) |
| `pad` | looped | detuned voices in one long seamless loop | `voices=5` (1..9), `detune=14` (0..60), `secs=3` (0.5..6), `shape=saw` (saw\|square\|triangle\|pulse), `base=C-4` (a note, C-4), `seed=7` (0..9999) |
| `noise` | looped | looped noise | `len=1` (0.1..4), `color=white` (white\|pink\|brown), `seed=8` (0..9999) |
| `kick` | one-shot | a falling sine with a click | `len=0.34` (0.05..2), `start=165` (40..400), `end=44` (20..120), `sweep=0.045` (0.005..0.3), `decay=0.11` (0.02..0.8), `drive=1.6` (0.5..4) |
| `snare` | one-shot | tone plus high-passed noise | `len=0.3` (0.05..1), `tone=185` (80..400), `seed=2` (0..9999) |
| `hat` | one-shot | metallic squares plus noise, closed | `len=0.075` (0.02..0.5), `decay=0.022` (0.005..0.3), `seed=3` (0..9999) |
| `openhat` | one-shot | the same, open | `len=0.45` (0.1..1.5), `decay=0.14` (0.05..0.6), `seed=3` (0..9999) |
| `clap` | one-shot | four noise bursts and a tail | `len=0.28` (0.05..1), `seed=4` (0..9999) |
| `tom` | one-shot | a falling sine; play it at several notes | `len=0.5` (0.1..2), `hz=130` (60..400) |
| `rim` | one-shot | a short two-tone click | — |
| `pluck` | one-shot, tuned | Karplus-Strong, tuned | `root=C-5` (a note, C-4), `len=1.6` (0.2..5), `damp=0.997` (0.9..0.9999), `bright=9000` (500..18000), `seed=5` (0..9999) |
| `bell` | one-shot, tuned | inharmonic partials, each with its own decay | `len=2.4` (0.3..6) |
<!-- /generated -->

### Instrument settings

<!-- generated:settings -->
| setting | values | what it does |
|---|---|---|
| `name=` | `text, _ for spaces` | the name Schism shows |
| `nna=` | `cut\|cont\|off\|fade` | what a new note does to the one still sounding: cut it, let it continue, key it off, or fade it |
| `dct=` | `off\|note\|sample\|inst` | duplicate check: look for a note/sample/instrument already sounding |
| `dca=` | `cut\|off\|fade` | what to do to a duplicate |
| `fade=` | `0..256` | fadeout speed after note-off; 0 = none |
| `pps=` | `-32..32` | pitch-pan separation |
| `ppc=` | `a note, C-4` | pitch-pan centre note |
| `gain=` | `0..128` | instrument global volume; the player applies it in steps of 2, so 0 and 1 are both silent |
| `pan=` | `0..64` | default pan 0..64 (omit for the channel's) |
| `rv=` | `0..100` | random volume variation, percent |
| `rp=` | `0..64` | random pan variation |
| `cutoff=` | `0..127` | filter cutoff 0..127 (omit: filter off) |
| `res=` | `0..127` | filter resonance 0..127 |
| `vol=` | `0..64` | default volume of its samples |
| `vib=` | `speed/depth/rate[/wave]` | sample auto-vibrato speed/depth/rate[/sine\|ramp\|square\|random]; rate is how fast it fades in, and 0 never starts it |
| `oct=` | `e.g. 1-6` | octaves a multisample covers, default 1-7 |
| `wave=` | see the waveform table | what the sound is made of |
| `sample=` | `text, _ for spaces` | use a sample defined by `smp` |
| `kit=` | `KEY:sample,KEY:sample,...` | key:sample pairs, one sample per key, all at natural pitch; a sample written @name is a forged one |
| `forge=` | `NAME`, `bank:NAME`, `kit:NAME` | a sound the forge makes: an archetype, or an instrument of a bank (`python3 -m schism forge archetypes`) |
| `dna=` | `axis:value,...` | with forge=, dials to move: axis:value,... (brightness:0.8,decay:0.3) |
| `reg=` | a note, `A3` | with forge=, the register it is built for, A3; for a drum the pitch it is tuned to |
<!-- /generated -->

### Envelopes

An envelope is `tick:value` nodes separated by commas, 25 at most, the first
at tick 0 and the ticks rising. A node ending in `s` marks the **sustain**
loop (the first and last marked nodes; the envelope holds there until the
note is released), one ending in `l` the **loop**. A tick lasts 2.5/tempo
seconds.

<!-- generated:envelopes -->
| setting | slot | value range | meaning |
|---|---|---|---|
| `venv=` | volume | 0..64 | volume 0..64 |
| `penv=` | pan | -32..32 | panning -32 (left)..32 (right) |
| `ienv=` | pitch | -32..32 | pitch in half-semitones: 32 is 16 semitones up |
| `fenv=` | filter | 0..64 | filter: share of `cutoff` in use, value/64 (64 = as set) |
<!-- /generated -->

`ienv` and `fenv` share one slot in the file (a flag says which), so an
instrument may have one of them. A `fenv` multiplies the instrument's
`cutoff`: the value is the share of it in use, 64 being the cutoff as set.
So `cutoff=127` lets the envelope be the cutoff (about twice its value),
and a lower `cutoff` is a ceiling the envelope never rises above. With no
`cutoff=` a `fenv` sets 127.

```sch
title Envelopes
tempo 120
channels 1
inst 1 name=Pluck wave=saw oct=2-5 nna=cut cutoff=127 res=40 fenv=0:8,6:50,40:14 venv=0:64,90:0 fade=64
inst 2 name=Swell wave=pad voices=5 detune=12 secs=3 base=C-4 nna=fade fade=30 venv=0:0,40:64,200:64s,240:64s penv=0:-20,120:20l,240:-20l
pattern a rows 32
C-4 01 v64 ...
rest 7
=== .. ... ...
rest 7
C-4 02 v48 ...
rest 7
~~~ .. ... ...
rest 7
end
order a
```

## Patterns

### Notes

`C-5`, `C#5`, `D-3`: letter, `-` or `#`, octave 0..9; `C-5` is middle C and
`A-4` is 440 Hz. Flats are accepted (`Db5`, `Bb3`) and case does not matter.

| written | means |
|---|---|
| `===` | key-off: release the note (the envelope sustain ends, the fadeout starts) |
| `^^^` | note cut: silence at once |
| `~~~` | note fade: start the fadeout at once |
| `...` | no note |

A key-off only silences an instrument that has `fade=` or a volume envelope
that ends at 0; otherwise the note rings on, and the compiler warns.

### Instrument and volume column

The second field is the instrument, `01`..`99`, or `..`. The third is the
volume column:

<!-- generated:volume -->
| written | means |
|---|---|
| `v00`..`v64` | set volume 0-64 |
| `p00`..`p64` | set panning 0 (left) - 64 (right) |
| `a00`..`a09` | fine volume up 0-9 |
| `b00`..`b09` | fine volume down 0-9 |
| `c00`..`c09` | volume slide up 0-9 |
| `d00`..`d09` | volume slide down 0-9 |
| `e00`..`e09` | pitch slide down 0-9 |
| `f00`..`f09` | pitch slide up 0-9 |
| `g00`..`g09` | tone portamento 0-9 |
| `h00`..`h09` | vibrato depth 0-9 |
<!-- /generated -->

### Effects

A letter `A`..`Z` and two hex digits, e.g. `H44`. All 26 letters are
written and read back exactly; what they *sound* like is the player's, and
docs/COVERAGE.md lists the ones that have been measured.

<!-- generated:effects -->
| letter | effect | parameter |
|---|---|---|
| `A` | set speed (ticks per row) | Axx  xx = ticks per row (A00 does nothing) |
| `B` | jump to order | Bxx  xx = order to jump to |
| `C` | break to row of the next pattern | Cxx  xx = row (hex) of the next order |
| `D` | volume slide | Dx0 up, D0y down, DFy fine down, DxF fine up |
| `E` | pitch slide down | Exx down xx a tick; EFx fine; EEx extra fine |
| `F` | pitch slide up | Fxx up xx a tick; FFx fine; FEx extra fine |
| `G` | tone portamento | Gxx  xx = speed toward the row's note |
| `H` | vibrato | Hxy  x = speed, y = depth |
| `I` | tremor | Ixy  x = ticks on, y = ticks off |
| `J` | arpeggio | Jxy  steps 0, +x, +y semitones, one a tick |
| `K` | vibrato + volume slide | Kxy  as H00 and Dxy together |
| `L` | tone portamento + volume slide | Lxy  as G00 and Dxy together |
| `M` | set channel volume | Mxx  xx = 00..40 |
| `N` | channel volume slide | Nxy  as D, on the channel volume |
| `O` | sample offset | Oxx  start xx*256 frames in |
| `P` | panning slide | Pxy  as D, on the panning |
| `Q` | retrigger | Qxy  every y ticks, volume change x |
| `R` | tremolo | Rxy  x = speed, y = depth |
| `S` | special (Sxy) | S1x glissando, S3x/S4x/S5x waveforms, S6x delay x ticks, S7x NNA and envelope, S8x pan, S9x surround, SAx high offset, SBx loop, SCx cut after x ticks, SDx note delay x ticks, SEx delay x rows, SFx macro |
| `T` | tempo | Txx  20..FF; T0x slides down, T1x up |
| `U` | fine vibrato | Uxy  as H, a quarter as deep |
| `V` | global volume | Vxx  xx = 00..80 |
| `W` | global volume slide | Wxy  as D, on the global volume |
| `X` | set panning 00-FF | Xxx  00 left, 80 centre, FF right |
| `Y` | panbrello | Yxy  x = speed, y = depth |
| `Z` | MIDI macro / filter | Zxx  macros; with IT's defaults Z00..Z7F sets the filter cutoff and Z80..Z8F the resonance |
<!-- /generated -->

## Timing

Impulse Tracker plays a row in `speed` ticks of `2.5/tempo` seconds. With
`rpb` rows to a beat, BPM = 24 × tempo / (speed × rpb). `tempo BPM` asks for
a BPM and the compiler picks the (speed, tempo) pair that gets closest,
preferring speed 6; the report says what it chose and what BPM that is.
Whole-number BPMs from 32 to 255 at `rpb 4` are exact. Because a tick is a
whole number of output samples in a player, a rendered song can differ from
the arithmetic by a tenth of a percent.

## Order

`order intro verse*2 chorus verse*2 chorus outro` plays patterns in that
order. A pattern that is defined but never ordered is an error (it would
not be played); so is an `order` naming a pattern that does not exist.

## What the compiler tells you

The compiler reports **every** problem in one pass, each with its line, the
text of the line, and the fix — a model that is told one mistake per round
needs ten rounds to repair a score with ten:

<!-- generated:badscore -->
```sch-bad
title bad
tempo 120
inst 1 name=Lead wave=saww
inst 2 wave=pulse duty=0.3 bar=2
pattern a rows 32
C-5 01 v64 ... | ...
rest 31
end
order a
```

```text
2 problems in the score:
line 3: wave: 'saww' is not one of sine, saw, square, triangle, pulse, fm, pad, noise, kick, snare, hat, openhat, clap, tom, rim, pluck, bell — did you mean saw?
  inst 1 name=Lead wave=saww
line 4: bar is not a setting of this instrument
  inst 2 wave=pulse duty=0.3 bar=2
  fix: settings for any instrument: cutoff, dca, dct, dna, fade, fenv, forge, gain, ienv, name, nna, oct, pan, penv, ppc, pps, reg, res, rp, rv, venv, vib, vol; wave=pulse also takes: duty
```
<!-- /generated -->

It also **warns** about things that compile and then play nothing, because
a player renders those without a word:

- a note on a channel before any instrument was set on it;
- a note the instrument has no sample for (a kit key you did not define);
- a key-off that cannot end the note;
- `gain=0` or `gain=1` (the player applies instrument volume in steps of 2,
  so both are silent);
- `vib=` with rate 0 (the vibrato never fades in);
- more than 99 samples or 200 patterns, which Impulse Tracker 2.14 itself
  cannot load (Schism Tracker and OpenMPT can; Schism stops at 235 samples
  and 240 patterns, and past those the compiler refuses).

```sch-warn
title Silent
tempo 120
channels 1
inst 1 name=Quiet wave=sine gain=1
pattern a rows 32
C-5 01 v64 ...
rest 31
end
order a
```
