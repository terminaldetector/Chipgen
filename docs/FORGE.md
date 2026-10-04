# The forge: instruments, drums and effects, made by measurement

An Impulse Tracker instrument is a sample, three envelopes, a filter and a
handful of switches, and nobody can say from those numbers what it sounds
like. The forge works from the other end. A sound is described by **eleven
dials** (brighter, snappier, more metallic, wider), every dial is *defined by
a measurement* of a note rendered by the real player, and a search moves the
dials, makes the sound, plays it through libopenmpt, reads the dials back off
what it heard, and keeps what is good. A model, if there is one, is asked
only which way to push the dials and which finalist to keep; everything else
runs offline, in plain Python, in seconds.

It is the same idea as the chip synthesis layer of Chipgen (same dials, same
definitions, same constants), with one difference that matters: an IT module
has one "chip", the sample, so what varies between sounds is not the hardware
but **the way the sample is made** — a loop built from a spectrum, damped
partials, layers of noise and sine — and each of those is a *family*.

```text
python3 -m schism forge compile bell --dna brightness=0.8 --mp3 bell.mp3
python3 -m schism forge kit mykit --style 808 --mp3 mykit.mp3
python3 -m schism forge analyse demos/forge_lab.sch --fit -o fitted.it
```

## The dials

<!-- generated:axes -->
| dial | 0 is | 1 is | how it is measured |
|---|---|---|---|
| `brightness` | dark, close to a sine | harsh and buzzy | spectral centroid / fundamental, 1x -> 0, 16x -> 1 (log) |
| `attack` | instant | a slow swell | 10%-90% rise time of the loudness envelope, log(1 + t / 2 ms): 0 ms -> 0, 400 ms -> 1 |
| `body` | dies away | holds at full level | level 0.6 s into the note (0.25 s after the peak for a slow swell) against the peak, -40 dB -> 0, 0 dB -> 1 |
| `decay` | a short ring | rings for seconds | time for the level to fall 63% of the way from the peak to the body, 30 ms -> 0, 3 s -> 1 (log); 1 when it hardly falls |
| `metallicity` | smooth, harmonic | clangorous, hollow, inharmonic | inharmonicity of the partials plus roughness among the upper ones |
| `detune` | one pitch | a wide ensemble | depth of the slow (0.7-3.5 Hz) amplitude beating of the lowest strong partial, read from a second or more of steady note: 4% -> 0, 44% -> 1 |
| `motion` | static | strong vibrato, tremolo or sweep | amplitude and pitch movement of the held note at 3.5 Hz and up |
| `bass_weight` | thin | sub-heavy | energy at or under 1.5x the fundamental against 1.5-4.5x, 0.2 -> 0, 100 -> 1 (log) |
| `articulation` | staccato, gated | legato, rings out after note-off | time to fall 20 dB after note-off, 20 ms -> 0, 1 s -> 1 (log); not measured when the note had already died |
| `noise` | tonal | noise | spectral flatness of the held note, 0.005 -> 0, 0.5 -> 1 (log) |
| `punch` | none | a strong attack transient | how many octaves higher the spectral centroid of the attack is than that of the body, 3 octaves -> 1 |
<!-- /generated -->

The definitions are the code (`schism/forge/descriptors.py`) and a test keeps
the words above and the numbers apart no further than a rounding. Three
things follow from them, and none is a bug:

- **Body and decay are coupled.** Body is the level 0.6 s into the note;
  decay is how long the fall from the peak to *that level* takes. So a decay
  longer than about half a second needs a body near the top (and then reads
  1.0: it hardly falls). The archetypes keep to what a note can measure.
- **Attack is blind under about 15 ms.** The loudness envelope is smoothed
  over 22 ms, so every attack shorter than that reads 0. The forge still
  makes the difference (a 3 ms ramp and a 12 ms ramp are different samples);
  the dial just cannot tell them apart.
- **Detune and motion need a second of steady note.** A beat of 1.3 Hz has
  to go round once to be told from the envelope's own curve. A sound that is
  gone in 300 ms has no detune reading rather than a false zero.

For a drum or an effect the "fundamental" the dials are read against is the
pitch it is *tuned to* (a kick at A1, a hat at A5, a snare at G4): a hat is
bright, noisy and short, a kick is bass-heavy with a punch, and the numbers
say so in the same words.

## The families

<!-- generated:families -->
| family | what it is | structure it can choose |
|---|---|---|
| `drum` | percussion as layers of sine, noise and metal: kick, snare, hat, clap, tom, rim, cymbal, shaker, cowbell (no detune, motion, articulation) | kind: `clap`, `cowbell`, `cymbal`, `hat`, `kick`, `rim`, `shaker`, `snare`, `tom` |
| `fm` | a four-operator FM voice with the registers of a YM2612, rendered to PCM at each octave: Mega Drive leads, basses, organs, pads |  |
| `fx` | one-shot effects: riser, impact, zap, swoosh (no detune, motion, articulation) | kind: `impact`, `riser`, `swoosh`, `zap` |
| `layer` | a stack of other instruments, PCM spliced by role: click, body, noise, sub, tail |  |
| `modal` | damped partials, each with its own fall: plucked strings, harps, pianos, marimbas, vibes, bells, glass | set: `string`, `tuned`, `bar`, `bell`, `plate` |
| `tone` | a loop built from a spectrum, with unison, vibrato, noise and an intro: leads, basses, pads, organs, keys, brass | motion_kind: `vibrato`, `tremolo`, `both`; shape: `saw`, `square`, `pulse`, `organ`, `reed`, `vowel`; unison: `3`, `2` |
<!-- /generated -->

What each dial does to the instrument, per family, is written at the top of
`schism/forge/fam_tone.py`, `fam_modal.py` and `fam_drum.py`. In short:

- **tone** builds one *loop* in the frequency domain (`spectral.py`): lines
  at whole numbers of cycles per loop, so the loop joins itself however
  strange its contents. Brightness is the roll-off of the partials, solved so
  that the spectral centroid is `16^brightness` times the fundamental;
  bass weight is the fundamental's gain; metallicity first stretches the
  partials off their multiples (a loop of 16 cycles lets a partial sit
  between two multiples), then roughens the upper ones; **detune** is unison
  voices a bin apart (a bin of a 32768-frame loop is 1.35 Hz) at the level
  that makes the beat as deep as asked; **motion** is Bessel sidebands, so a
  vibrato of 14 cents is 14 cents; **noise** is random-phase lines in every
  bin. The attack ramp, a bright transient (`punch`) and any fall faster than
  120 ms are baked into the *intro* of the sample, so they are the same
  length at every tempo; everything slower is the volume envelope.
- **modal** is a sum of damped sinusoids, built in the time domain: a string
  is the harmonics, a marimba bar is 1 : 4 : 10, a bell the ratios of a
  bell. `decay` is the fall of the fundamental and `punch` is how much faster
  the top falls, so a pluck darkens as it rings. Detune makes every mode a
  close pair (a piano's strings); motion is a vibraphone's fan.
- **drum** and **fx** are lists of layers (`layers.py`): a sine that falls in
  pitch, noise through a filter that may sweep, squares at the 808's ratios,
  a click. A kick, a snare, a hat are three to four of the same parts.

## Making an instrument, and why it is right the first time

A genome is a DNA plus a few structural choices; `compile` turns it into a
**patch**: a recipe for the samples (never PCM — the same recipe makes the
same bytes, so a bank of seventy-five instruments is 250 KB of JSON), the
envelopes **in seconds**, and the instrument's switches. A tick is `2.5 /
tempo` seconds and the tempo belongs to the song, so the patch becomes ticks
only when it is installed in a module, at that module's tempo.

The first compile is already close, because the arithmetic for each dial is
the descriptor's own formula run on a list of partials (`tonelib.py`). What
is left is a *controller*: render, read the dials, move a per-dial bias
toward the error (the first step by the error, then the secant, bracketing
when the readings straddle the target, freezing a dial that does nothing),
keep the best attempt, at most three or four renders. **Instruments are then
trimmed to one loudness** — the RMS of the loudest 120 ms of the probe note,
0.03 in a module with mix volume 48 — by setting the instrument's gain (a
sample is stored at 90% of full scale, so gain can only turn it down; one
that cannot reach the level at full gain is reported `soft`).

## In a score

```sch
title Forged
tempo 112
channels 3
inst 1 name=Bass forge=punch_bass
inst 2 name=Pad forge=strings oct=2-5
inst 3 name=Kit forge=kit:tight_kit
pattern a rows 32
C-2 01 v64 ... | C-3 02 v40 ... | C-2 03 v64 ...
... .. ... ... | E-3 02 v40 ... | ... .. ... ...
rest 3
G-2 01 v60 ... | ... .. ... ... | D-2 03 v60 ...
rest 3
C-2 01 v64 ... | G-3 02 v40 ... | F#2 03 v40 ...
rest 22
end
order a
```

`forge=NAME` names an instrument of a bank the score loaded, then of the
**starter bank** that ships with the forge (instant: the patch is in the
file), then an archetype (the dials are realised against the player first,
which takes a few tenths of a second, once per distinct sound in a process).
`forge=kit:NAME` is a drum kit: one instrument with a drum on each key.
Everything else on the line is the ordinary instrument settings
(`name nna dct dca fade pps ppc gain pan rv rp vol cutoff res vib oct venv
penv ienv fenv`), applied on top. Two more:

```sch-forge
title Moved dials
tempo 120
channels 2
bank mine.bank.json
inst 1 name=Bell forge=bell dna=brightness:0.8,decay:0.4 reg=A4 oct=3-6
smp thump forge=kick dna=punch:0.9
inst 2 name=Kit kit=C-2:thump,D-2:@snare,F#2:@hat
pattern a rows 32
E-5 01 v48 ... | C-2 02 v64 ...
rest 31
end
order a
```

`dna=axis:value,...` moves dials and `reg=` sets the register — for a drum,
the pitch it is tuned to. `smp NAME forge=...` makes a named one-shot (a
drum, an effect, a struck or plucked sound; not a tone, which is a loop and
needs a sample per octave), and a kit key may name a forged one-shot as
`@name`. `bank FILE` loads a bank written by `forge run`; a relative path
starts where the score is. The warnings of the score compiler work as they
do for any instrument: a note outside the octaves a forged instrument was
built for is reported as silent, with the fix (`oct=`).

## Banks, scenarios, search

A **bank** is JSON: for each instrument the genome that made it, the patch it
compiled to, what the probe read off it and why it was kept. A **scenario**
is the other half, a description of a run:

```json
{"family": "tone", "role": "lead",
 "seeds": ["saw_lead", {"archetype": "brass", "dna": {"detune": 0.4}}],
 "objective": {"target": {"brightness": 0.85, "metallicity": 0.2},
               "box": {"detune": [0.3, 0.6]}},
 "register": ["A", 4], "generations": 6, "offspring": 8, "keep": 4,
 "seed": 1}
```

The fields: <!-- generated:scenario_keys -->
`archive`, `family`, `format`, `generations`, `keep`, `kind`, `locked`, `max_probes`, `min_distance`, `mix`, `name`, `note`, `novelty`, `objective`, `offspring`, `prefix`, `realise_probes`, `register`, `role`, `seconds`, `seed`, `seeds`, `sigma`
<!-- /generated -->. A seed is an archetype (with dials moved), a DNA, a bank,
or `{"recipe": "wave=saw oct=3-5 fade=60"}` — **an `inst` line from a score,
played and measured**, which is how an instrument somebody wrote by hand
becomes a parent. A target written as a few dials means *only those*; a `box`
gives a range.

The loop is *seed → mutate → render probe → validate → score → retain*:

- a child moves one to three dials of its parent by a Gaussian step
  (reflected at the ends of the range), or flips a structural choice, or
  crosses two parents; when the objective has a target, half of the moves
  drift toward it, so a search with a goal walks instead of wandering;
- every candidate is rendered, measured and *validated* (audible; not
  clipping; on the pitch asked; a loop that joins; a sample that ends
  quietly; legal for the file format) before it is scored;
- the score is 0.70 fit to the objective, 0.15 fidelity (does it sound like
  its own DNA), 0.05 quality, plus novelty against what is kept;
- the archive keeps the best that are not copies of each other (a minimum
  distance in *measured* dial space), and only the finalists get the long
  probe that detune and motion need.

A **director** is optional: `HeuristicDirector` (no model) reads the gap
between the best and the target and pushes the other way; `FileDirector`
reads directions written beforehand (`forge director-prompt` prints the
question, paste it into any chat window, save the JSON reply); `LLMDirector`
asks an OpenAI-compatible server (Ollama, llama.cpp, LM Studio, a cloud API)
and *never raises* — a model that is down, slow or answering in prose costs
the run that answer and nothing else. What a model says is validated before
the search sees it: dials the family cannot express are dropped, steps are
clipped, the JSON is read through fences, trailing commas and single quotes.

## Mixing

```json
{"mix": [{"from": "slap_bass", "weight": 2},
         {"mix": [{"from": "bell"}, {"from": "brass"}], "weight": 1}],
 "mode": "auto"}
```

`from` names a bank instrument, a starter instrument, an archetype
(`archetype:NAME` or the bare name), or `recipe:wave=saw ...`. **dna** mode
averages what the parents *sound like* and compiles afresh, so it works across
families and for a hand-written recipe, and keeps nothing of either; **morph**
mode mixes the recipes (a tone partial by partial, a modal instrument mode by
mode, a drum layer by layer, envelopes point by point), which keeps the
lumpy, specific character of each, and can fail — the result is played and
checked, and falls back to dna if it is silent, illegal or off pitch.
**layer** mode keeps the *samples* (next section), and it is what `auto` does
when the parts are not the same kind of sound: a tone and a drum, a kick and
a snare. Two tones, two bells, two kicks are still morphed; `--mode dna` asks
for the old blend of dials by name. A mix of mixes is a tree, evaluated from
the leaves, up to six deep.

## Layers: a stack, not an average

A Schism instrument is a stack. Average the dials of an `acid_bass` and a
`kick_hard` and what comes out is a duller bass with no kick in it, because
the average keeps neither sample. A **layer** takes the samples and puts each
where it belongs in time, as PCM:

```text
python3 -m schism forge layer clap@click snare@body --name crack --out mine.bank.json
python3 -m schism forge layer kick_hard@click acid_bass@body --stereo 6
python3 -m schism forge layer snare+clap --roles click,body
```

A part is `NAME`, `NAME@ROLE` or `NAME@ROLE/ms=40/db=-3`; a name is a bank
entry, a starter instrument, an archetype or `archetype:NAME`. A part with no
role gets one from what the sounds are (the one that loops, or the longest
pitched one, is the body; the shortest of the rest is the click; a noisy drum
is noise; a low one, sub). `--roles` gives them by position, so Grok's
`snare+clap --roles click,body` makes the *snare* the click; to put the clap's
crack on a snare's body, say so with the roles: `clap@click snare@body`.

| role | what it takes from its sound | options (default) |
|---|---|---|
| `click` | the first `ms` of it, at a fixed gain, handing over to the body | `ms` (30), `fade` (15) |
| `body` | all of it; it holds the pitch, the loop and the volume envelope. With a click over it, it comes in at `from` across the hand-over (equal power: the click falls as a cosine, the body rises as a sine) | `from` (20) |
| `noise` | its top end, high-passed at `hp`, laid over the hit from `at` for `ms` | `hp` (1500 Hz), `ms` (400), `at` (0) |
| `sub` | its bottom, low-passed at `lp`, laid under the hit | `lp` (150 Hz), `ms` (500), `at` (0) |
| `tail` | what follows the body, at `level` dB against the body's loudest 120 ms; a tail that loops is the stack's loop | `at` (when the body has fallen to a third), `level` (-9), `ms` (1500) |

Every part also takes `db` (its gain in dB; `weight` in a mix recipe gives the
same thing as a ratio). The click's 30 ms are the first 30 ms of its sound
*in real time*: every part is converted to the rate the body plays at, so 30
ms is 30 ms whatever the sample rate of either is. The stack is one sample for
each band of keys, like any forged instrument. A drum in a stack is kept at
its recorded pitch whatever key is pressed (the band's centre plays it as
recorded, the keys round it transposing it as a sampler does); a pitched part
follows the key.

What a stack will not do, and says so:

- **One loop.** A sample has one loop, so only one part of a stack can loop:
  the body, or, if the body is a one-shot, a looped tail (a kick, then a pad).
  A body that loops keeps its loop whole: the front that plays once is made
  longer by *whole repeats of the loop* until the click and the rest fit in
  it, which changes nothing that is heard. Two looped sounds are refused with
  the reason, and `mix` (same family) is the answer.
- **One envelope.** The instrument's volume envelope, NNA, filter and
  switches are the loop-owner's, and its slow attack is the stack's slow
  attack: a click under a pad that fades in over half a second fades in too.
  The compiler says so in the notes; write the envelope yourself
  (`venv=`) or choose a body that starts at once.
- **Levels** are the parts' own: each is calibrated to the same loudness, so
  the stack plays at about the level of its loudest part. `db=` moves one.

### Chords, stereo pairs, backwards

Three edits any forged sound may be given, in a score or in a layer. All
three keep the loudness of the plain sound by raising the instrument gain
where there is room (a chord is a sum of notes and has a lower RMS for the
same peak) and say how far it fell short where there is not.

| setting | what it makes |
|---|---|
| `chord=min9 root=-3` | a chord kept as one key: each key plays the chord *on that key*, `root` being where the chord's root sits against the key (here A minor 9 on C). A name (`maj min dim aug sus2 sus4 5 6 7 maj7 min7 min9 ...`) or semitones above the root, `0,3,7,10,14`. A tone's lines are put on the chord's notes in the spectrum of a 65536-frame loop (so it loops, and each note is within a few cents of equal temperament: the loop holds the count of cycles that suits the chord best); a struck sound is its notes read faster and slower and summed, and dies away |
| `stereo=6` | a stereo sample: the sound twice, a little flat on the left and sharp on the right. A struck sound is read slower and faster by 6 cents; a *loop* can only move in whole steps of its pitch (one step is 1/cycles of the pitch, 3 to 40 cents depending on the note), so it gets the nearest it can hold, and says which. Channel pan is not a substitute: this is the width of the sample |
| `rev=on` | the sample backwards (a reverse cymbal). A looped sound is played out for a second and a half first and does not loop |

In a score they go on an `inst` line, or on an `smp` line for a one-shot:

```sch
title Layers
tempo 120
channels 3
inst 1 name=Crack forge=layer:clap@click+snare@body
inst 2 name=KickBass forge=layer:kick_hard@click+acid_bass@body oct=1-3
inst 3 name=Stab forge=epiano chord=min9 root=-3 stereo=6 oct=3-5
smp crash forge=cymbal rev=on
pattern a rows 32
C-5 01 v64 ... | C-2 02 v64 ... | C-4 03 v50 ...
rest 31
end
order a
```

In a layer, `chord` goes to the body (a click is not a chord), `rev` to the
whole stack, and `stereo` to the whole stack if it is a one-shot, or to the
loop-owner if it loops (and only a tone can be widened and keep its loop).
In a score the parts of a layer are joined with `+` and an option follows a
`/`: `forge=layer:clap@click/ms=40/db=-3+snare@body`. A stack made in a score
or a bank can be named in a kit through `smp`:
`smp crack forge=layer:clap@click+snare@body`, `inst 4 kit=D-2:crack`. A recipe
for the mixer, with nested parts:

```json
{"layer": [{"from": "clap", "role": "click", "ms": 25},
           {"mix": [{"from": "snare"}, {"from": "tom"}], "role": "body"}],
 "stereo": 6}
```

## The ladder: NES, Mega Drive, Schism

A module made of `wave=square` and `wave=pulse` is heard as an NES expansion
chip however its notes are arranged, and mixing saw archetypes does not cure
it: the sound is still a pulse. What cures it is to keep the *voice* and change
the driver it is played by. `forge lift-voice` pushes one voice up three
rungs, each a rule written down, none a model:

```text
python3 -m schism forge lift-voice lead --from nes --to sega
python3 -m schism forge lift-voice lead --from sega --to schism --demo lead.sch --mp3 lead.mp3
python3 -m schism forge lift-voice organ --to schism          # both lifts
```

`NAME` is a role (`lead bass pad organ stab bell snare kick hat`) or an
archetype (`saw_lead` is a lead, `acid_bass` a bass, `open_hat` a hat). The
command keeps the voice in `NAME.voice.json` (every rung it has reached), puts
the Mega Drive and Schism rungs in `NAME.bank.json` as `NAME_sega` and
`NAME_schism` (`bank NAME.bank.json` and `forge=NAME_sega` in a score), and
with `--demo` writes a score that plays *one note list* on each rung, one after
the other: the NES instrument, the Mega Drive voice, the Schism instrument.

**Rung 1, the NES.** A voice is `{duty, noise_mode, arp_intervals}` (and a
volume envelope in frames): a pulse at 12.5, 25, 50 or 75 % (a 75 % pulse is a
25 % pulse upside down: the same buzz), a triangle, or the noise channel in its
long or its metallic short mode. Harmony is an arpeggio, never a chord sample,
and **there is no pad**: the voice of a pad is `arp_intervals [0, 4, 7]`, and
the command prints the pattern for it (`C-4 01 v40 J47` on every row: `Jxy`
steps 0, +x, +y semitones a tick). The rung's instrument is the existing
`wave=pulse duty=...` recipe; it is the baseline the other two are measured
against, not an emulation of the chip.

**Rung 2, the Mega Drive.** The YM2612 is six channels of four sine operators,
and a voice is its registers: an algorithm, a feedback level on operator 1 and,
for each operator, `MUL DT TL KS AR D1R SL D2R RR` in the chip's own units
(`fm.py`). FM as FM: nothing here imitates the pulse, what the duty did to
the spectrum an operator does with its level.

| the NES voice | becomes | because |
|---|---|---|
| pulse lead, duty 25 / 50 % | algorithm 4, feedback 5 on operator 1, a detune of +3 and -3 on the carrier pair, a short decay on the modulators | two pairs; the feedback is the pulse's edge, the decays its bite; the narrower the duty, the stronger the modulator (TL 32 / 40) |
| pulse lead, duty 12.5 % | algorithm 5, feedback 6 | one modulator into three carriers: the brightest |
| triangle bass | algorithm 0, multiples 1, 1, 2 into a carrier at 1, short decays on the modulators | a chain, low multiples: a clean fundamental with a bite |
| pad (a pulse arpeggio) | algorithm 4, two carriers detuned a step either way, a slow attack | one held voice instead of an arpeggio |
| organ | algorithm 7 (all carriers), drawbar multiples 1, 2, 3 and a fourth operator at multiple 6 that attacks at once and falls to nothing in a few milliseconds | the key click; the chip has four operators, so of the usual 1, 2, 3, 4, 6 the 4 is left out |
| stab | algorithm 5, every envelope falling to nothing | struck, not held |
| bell | algorithm 4, a high multiple (7) and 3 into two carriers, falling to nothing | |
| noise snare, kick, hat | **not an FM voice**: a DAC one-shot, 8 bit, 13.3 / 11 / 16 kHz | the console plays drums through one DAC channel |

There are no stereo samples on the console, so the voice's panning is the
chip's register (`L`, `R`, `LR`, which sets the instrument's default pan). The
voice is played by an IT instrument all the same: it is rendered to PCM at every
octave, as a loop that joins itself if it holds (an operator with no second
decay holds; one whose sustain level is 15 is gone) and as a one-shot if it
dies away. The operator numbers are kept in the recipe of the instrument and
written to the **sidecar** (`SONG.it.json`, `"operators"` for an FM voice,
`"dac"` for a drum), so `build --preset sega` exports the voice and not only
its PCM.

**Rung 3, Schism.** Built from the Mega Drive voice rendered to PCM, not from
the NES cycle: an octave a sample; a loop on what is held, a one-shot on what is
struck; the voice twice, 6 cents flat and 6 sharp, as a stereo sample (not on
the bass: the low end stays centred); then a post stack, a soft clip, a small
cabinet, and a chorus **on the organ only**, all applied so that a loop is still
a loop; NNA by role (`cut` drums, leads and basses, `fade` pads and organs,
`cont` bells); and a chord on one key for the stab (`chord=min7`). A drum is a
stack (see Layers): the DAC one-shot as the noise (the crack) over the starter
bank's drum of that kind as the body.

What it is held to (`tests/test_ladder.py`, which reads samples and, where a
player is installed, renders):

- **no duty buzz**: the harmonics of a NES pulse follow its duty cycle
  (`|sin(pi n d)| / n`; the log-amplitude correlation with the rendered pulse is
  0.99 to 1.00); those of the Mega Drive and Schism rungs correlate at 0.2 to
  0.3;
- **the organ has a key click**: at six times its pitch the first 12 ms hold
  8 to 13 times (Mega Drive) and 3.5 to 4.7 times (Schism, whose drive puts a
  little of that pitch into the held notes) the energy of the held notes; a NES
  organ holds the same at the front as behind;
- **the snare has a noise crack and a body**: the forge reads the DAC snare at
  `body 0.00` and the Schism snare at `0.12` (the starter snare is `0.16`), with
  `noise 0.73`; in the samples the crack is at the front (2 to 9 kHz) and the
  tone under it (120 to 400 Hz) is at least half again as strong as the DAC's.

What it does not do: the FM renderer is an **approximation of the chip, not an
emulation** — the operator numbers are exact and the register-to-seconds scale
was set by the published decay tables and by ear; there is no LFO, no SSG-EG and
no DAC quantisation of the FM output; Chipgen's own YM2612 emulator is where a
dump is checked against the real thing. A detune is **whole bins of the loop**:
DT's share of an operator's frequency, rounded, which is a bin or two at A4 and
A5 and none at A2, where a bin is a quarter of a semitone (so a lead beats in
its upper octaves and not in its lowest). The stereo Schism rung is big, about
a megabyte for a held voice, because a loop that can be 6 cents either side is
65536 frames. Nobody has listened.

## Rearranging: a second pass that rewrites

`analyse` labels the parts of a module. `rearrange` acts on the labels: it takes
a `.it` or a `.sch` and writes another module, with a diff of roles, that
plays **the same notes** with other sounds on other channels.

```text
python3 -m schism forge rearrange song.sch --to schism            # song.schism.it (+ .json)
python3 -m schism forge rearrange song.it --to sega --roles ch03=bass --echo 1:2:-14
```

| operation | what it does | flag |
|---|---|---|
| **remap** | every melodic part gets the voice its job has on the rung (lead and counter-melody: the lead; harmony and pad: the pad; bass: the bass; arp: a struck bell on Schism, the PSG's pulse on the Mega Drive). Only the instrument number in the cells changes. A one-shot drum on a channel of its own (a `wave=kick`, a looped noise under a dying envelope, played on one or two pitches: *whatever the arrangement calls it by its notes*) becomes the rung's drum of its kind (kick, snare, hat by how it sounds) set to play as recorded on every key, so its notes stay | `--to nes\|sega\|schism` |
| **register** | a part that is the bass and sits above C-3 drops by octaves into the bass's range, without leaving the keyboard, and a **sub** (the bass voice, an octave under) follows it on a channel of its own. A bass above its instrument's range is a silent note | `--no-register`, `--no-sub`, `--roles ch03=bass` |
| **split** | an arpeggio channel (the NES does a chord on one) keeps its notes, a little quieter, and gets a **pad** under it: for each window of a measure the chord it plays, its three commonest pitch classes one octave down, held on three added channels panned left, centre and right. On Schism an **echo** of the arp follows, 2 rows later at -14 dB. Not on the NES, which has no channels to spare | `--no-split` |
| **echo** | channel N copied to a free one, some rows later, some dB quieter, panned to the other side, effects that shape a note (vibrato, slides, arpeggio) copied with it. A copy does not cross the end of its pattern | `--echo N[:ROWS[:DB]]` |
| **fill** | the last four rows of a drum pattern that ends a phrase (every fourth, and the last) and has no roll get one: snares, then toms high to low, **using only the keys the kit has** (a kit with neither gets none, and the diff says so). The roll goes on the channel of the kit that plays the snare; a pattern played more than once is copied for the one that gets the fill | `--no-fill`, `--every N`, `--kit tight_kit` |

**Notes are the caller's.** The pass changes timbre, the split of channels and
the register of a bass. The notes of the lead, the counter-melody, the harmony
and the arpeggio come out exactly as they went in (a test compares them
note for note); what the pass adds, a sub, a pad, an echo, a fill, is made of
notes the song had or keys the kit has. It does not write a melody and it is not
for turning a tune into somebody else's.

Every console has a number of channels, and the pass keeps to it: four on the
NES, seven on the Mega Drive (six FM voices and a DAC, as `--preset sega` has
it), sixty-four on Schism. An addition that would not fit is left out and the
diff says so (`no room to split the arpeggio into a pad: the sega has 7
channels`). Like `build`, it fits the mix volume to a peak of 0.89 (`--peak
off` leaves it), writes `SONG.RUNG.it` and a sidecar with the diff in its
notes and the operator dumps of the FM voices, and with `--mp3` renders it.

What it does not do: the classification is the arrangement's guess from the
shape of the notes (name a channel's job with `--roles`); a drum kit is kept
and filled but not rewritten (`--kit` swaps it for a starter kit); the pad of a
split holds a triad, not the arpeggio's every pitch; voicing, density and the
choice of which parts to double are the rules above and nothing more.

## Drum kits

A kit is drums that leave room for each other. Each drum is searched on its
own, from an archetype moved by the style, and the few best are kept; then
the kit is chosen from those by the same measure the arrangement model uses
for parts — a drum is added if it is good *and* its spectrum, set against the
drums already chosen, shares little of the same Bark bands.

<!-- generated:kit_styles -->
| style | what it changes |
|---|---|
| `808` | kick: decay +0.25, bass_weight +0.05, brightness -0.05, punch -0.2; snare: noise -0.2, metallicity +0.15, decay +0.05; hat: metallicity +0.2, brightness +0.05; clap: decay +0.1; tom_low: decay +0.15; tom_high: decay +0.1; cowbell: decay +0.1 |
| `big` | all: decay +0.12, brightness -0.1; kick: bass_weight +0.04, decay +0.1 |
| `bright` | all: brightness +0.12, punch +0.1 |
| `dark` | all: brightness -0.2; hat: brightness -0.1; kick: bass_weight +0.03 |
| `lofi` | all: brightness -0.25, noise +0.15, decay +0.05; kick: punch -0.2 |
| `tight` | all: decay -0.12, punch +0.1; hat: decay -0.05 |
<!-- /generated -->

<!-- generated:kit_roles -->
| drum | archetype | key |
|---|---|---|
| `kick` | `kick` | `C-2` |
| `snare` | `snare` | `D-2` |
| `clap` | `clap` | `D#2` |
| `hat` | `hat` | `F#2` |
| `open_hat` | `open_hat` | `A#2` |
| `tom_low` | `tom` | `F-2` |
| `tom_high` | `tom` | `A-2` |
| `rim` | `rim` | `C#2` |
| `cymbal` | `cymbal` | `C#3` |
| `shaker` | `shaker` | `G#2` |
| `cowbell` | `cowbell` | `G#3` |
<!-- /generated -->

## Arrangement: where an instrument can sit

`python3 -m schism forge analyse SONG.sch` (or a `.it` file) looks at the
channels together. Each channel is a **part** (register, density, how long it
holds against the gaps, a guessed job: bass, lead, pad, arp, counter,
harmony, drums); each instrument it plays is probed at the part's register
(any instrument of any module, not only forged ones) and gives its spectrum
in semitone bands, attack, fall, sustain, release and loudness; the two make
a **footprint** — per row and per ear, how much energy the part puts in each
of 24 Bark bands — without rendering the song. The conflicts named:

<!-- generated:conflicts -->
| conflict | what it names |
|---|---|
| `masking` | two parts share most of their spectrum at the same times, and one is quieter |
| `bass_stack` | two parts below D-3 share their low bands |
| `mud` | a part above the bass puts a large share of its energy under 300 Hz |
| `sustain_blanket` | a part that holds long notes sits within an octave of a part that moves |
| `onsets` | most notes of two sharp-attacked parts start on the same row |
| `clash` | notes a second, tritone or seventh apart in two parts (worse if both timbres are metallic) |
| `dynamics` | a part is louder than its job allows against the lead |
| `pan_stack` | three or more parts in the middle that share a spectrum |
| `voices` | more than 32 voices sound at once (NNA continue/fade instruments pile up) |
<!-- /generated -->

`--measure` replaces the estimated levels by real ones, each channel rendered
alone through libopenmpt. `--fit` repairs: each round tries the most pressing
suggestions on the module itself, at two strengths, with real probes — a pan,
a level, an octave, a change of DNA for a forged instrument (re-realised and
swapped in), or, for an instrument nobody forged, the dials its switches
reach (the volume envelope for attack, body, decay and articulation; the
filter for a darker brightness) — keeps the one that lowers the cost most,
and stops when nothing lowers it by 0.03. On `demos/forge_lab.sch` it takes
the cost from 7.2 to 1.4 in three changes (the strings hold shorter notes so
the lead can be heard between them; the harp and the lead soften their
attacks so the hits stop stacking) and prints what is left: the impact still
shares the bass's low bands, two stretches where the strings and the lead
play a second apart, which no dial can mend (they are notes), and the bell
and the impact starting on the same rows. The module it writes is a module.

## The command line

<!-- generated:forge_cli -->
```text
axes                      the dials and what each one means
families                  the ways a sound is made, and their kinds
archetypes                named starting points
compile NAME              one DNA -> an instrument, asked against heard
survey                    every archetype, asked against heard
sweep FAMILY AXIS         one dial from 0 to 1, everything else held
run SCENARIO.json         search: seed -> mutate -> probe -> validate ->
                          score -> retain, into a bank
check SCENARIO.json       read a scenario and say what it would do
template FAMILY           a scenario to start from
mix A B [C...]            blend instruments (or a recipe tree) into one
layer A@click B@body      stack instruments: PCM spliced by role, not averaged
kit NAME --style S        a drum kit whose drums leave room for each other
show BANK                 read a bank
play BANK [NAME...]       render the instruments to audio, to listen
analyse SCORE             where do the channels of this module sit?
lift 'wave=saw oct=3-5'   measure an `inst` line and print its DNA
rearrange SONG --to schism
                          rewrite a module for another rung: other
                          voices, a split arpeggio, an echo, a fill
lift-voice NAME --from nes --to sega
                          one voice up the ladder NES -> Mega Drive ->
                          Schism: operator numbers, DAC drums, PCM zones
starter                   rebuild the bank that ships with the forge
director-prompt SCENARIO  the question a model would be asked
```
<!-- /generated -->

## The archetypes

<!-- generated:archetypes -->
| name | family | tuned to | plays | what it is |
|---|---|---|---|---|
| `clap` | drum | D4 | drums | a hand clap: four bursts and a tail |
| `cowbell` | drum | G4 | drums | two square tones through a band-pass |
| `cymbal` | drum | A5 | drums | a crash: a long wash of metal and noise |
| `hat` | drum | A5 | drums | a closed hat |
| `kick` | drum | A1 | drums | a kick: sine falling from a click |
| `kick_hard` | drum | A1 | drums | a hard kick with a long beater click |
| `open_hat` | drum | A5 | drums | an open hat |
| `rim` | drum | A4 | drums | a rim click: two short tones |
| `shaker` | drum | A5 | drums | a shaker: a soft burst of filtered noise |
| `snare` | drum | G4 | drums | a snare: noise with a crack and a body |
| `tom` | drum | A2 | drums | a tom: sine falling gently |
| `impact` | fx | A1 | fx | a boom: a falling sine and a noise burst |
| `riser` | fx | A3 | fx | noise rising in a sweep, then cut |
| `swoosh` | fx | A3 | fx | a pass of noise, up and away |
| `zap` | fx | A4 | fx | a laser: a fast fall in pitch |
| `bell` | modal | A4 | lead, harmony | a bell: bright, metallic, rings for seconds |
| `glass` | modal | A5 | lead | a struck glass |
| `harp` | modal | A3 | harmony | a harp: bright start, long gentle ring |
| `kalimba` | modal | A4 | lead | a thumb piano: dull thunk, clear ring |
| `marimba` | modal | A3 | lead, harmony | a wooden bar, fundamental and a tenth |
| `piano` | modal | A3 | harmony, lead | a struck string pair: bright thump, slow beating fall |
| `pluck` | modal | A3 | harmony, lead | a plucked string |
| `vibes` | modal | A3 | lead, harmony | a metal bar with a motor tremolo |
| `acid_bass` | tone | A2 | bass | a squelchy saw bass that closes as it falls |
| `blip` | tone | A4 | lead | an arcade blip with a chirp |
| `brass` | tone | A3 | lead, harmony | a section that leans in after the attack |
| `choir` | tone | A3 | pad, harmony | a vowel pad |
| `epiano` | tone | A3 | harmony, lead | an electric piano that rings and fades |
| `glass_pad` | tone | A4 | pad | a cold, slightly inharmonic pad |
| `organ` | tone | A3 | harmony, pad | a drawbar organ that holds until the key comes up |
| `pad` | tone | A3 | pad, harmony | a slow swelling ensemble pad |
| `punch_bass` | tone | A2 | bass | a punchy bass with a bite at the front |
| `reed` | tone | A3 | lead, harmony | a clarinet-ish reed |
| `saw_lead` | tone | A4 | lead | a sawtooth lead with a little ensemble and vibrato |
| `slap_bass` | tone | A2 | bass | a funk bass: bright attack, clean body |
| `square_lead` | tone | A4 | lead | the chiptune square |
| `stab` | tone | A3 | harmony | a short metallic hit for the off-beat |
| `strings` | tone | A3 | pad, harmony | bowed strings: slow, wide, a little rough |
| `sub_bass` | tone | A2 | bass | a pure sine under everything |
| `supersaw` | tone | A3 | lead, pad | a wall of detuned saws |
| `whistle` | tone | A5 | lead | a near-sine with breath and vibrato |
| `wobble_bass` | tone | A1 | bass | a sustained bass with a fast tremolo |
<!-- /generated -->

Every number in the table is an opinion about what that kind of sound
measures; `python3 -m schism forge survey` renders each and prints asked
against heard, with `!` where the family could not get within 0.15.

## What it does not do

- **Nobody has listened.** The dials are measurements, the tests hold every
  claim here to them, and both players (libopenmpt and Schism Tracker) agree
  on the level of a forged module (within 0.1 dB on the two modules
  measured; the test allows 1.5). Whether a forged bell is a good bell is a
  question for ears; the starter bank and `forge_lab.mp3` are there to be
  listened to.
- **Archetypes are opinions.** Some dials cannot be reached from some
  structures (a sine cannot be bass-light; a band-limited snare reads no
  noise); the survey shows where, and the closed loop does its best.
- **Pitch scales everything inside a sample.** A chorus beating at 1.35 Hz at
  the base of its octave beats twice as fast an octave up; a baked fall is
  half as long an octave up. That is how a sampler behaves; each octave of a
  loop has its own sample so the drift stays within a factor of 1.4.
- **A `.it` cannot hold more than 235 samples in Schism**, and a long loop is
  64 KB a sample; `oct=` narrows what a forged instrument covers.
- The IT compressed sample format and MIDI macros are not written (see
  COVERAGE.md); `pan` is a channel and instrument setting, and a pan envelope
  is not forged. Stereo is written and read: planar, all of the left and then
  all of the right, which both players read (an interleaved frame order
  plays as noise, measured).
- **A stack has no dials.** The arrangement fit cannot move a stack's
  brightness; it moves the parts' (re-forge them) or leaves the stack alone.

## Files

`schism/forge/`: `dsp.py` (FFT and filters), `spectral.py` (loops from
lines), `modal.py`, `layers.py`, `tonelib.py` (dial arithmetic), `dna.py`,
`descriptors.py` (the definitions), `probe.py`, `validate.py`, `score.py`,
`genome.py`, `patch.py`, `family.py`, `fam_*.py` (`fam_layer.py` is the
stack, `fam_fm.py` the FM voice), `fm.py` (the four-operator renderer),
`ladder.py` (NES, Mega Drive, Schism), `rearrange.py` (the second
pass), `splice.py` (PCM cut, joined,
chorded, widened, reversed, driven),
`archetypes.py`,
`evolve.py` (controller, archive, search), `bank.py`, `mixing.py`,
`director.py`, `llm.py`, `kits.py`, `arrangement.py`, `audition.py`,
`notation_hook.py`, `cli.py`, `banks/starter.bank.json`.
