# Making new instruments: the synthesis layer

The bank has 19 YM2612 patches, 8 OPL2 patches and no NES instruments at all
(the 2A03 has no instruments, only a driver that plays macros). If the sound
you want is not in it, you have two choices: write operator registers by hand,
or describe the sound and let the engine build it. This is the second.

Everything here is local and offline. It uses the same emulators that render
your song: a candidate instrument is played, measured, and judged by numbers.
**No language model is needed**, and none is asked about any single change.

```
python3 python/forge.py axes          # the dials, what they mean, what each chip can reach
python3 python/forge.py compile opl2 --archetype bell --dna brightness=0.8
python3 python/forge.py run python/synthesis/scenarios/ym2612_lead.json --out work/lead.bank.json
python3 python/chipgen.py work/song.trk --forge-bank work/lead.bank.json
```

Your files go in `work/` as always. You never edit `python/`.

## The dials

Eleven numbers from 0 to 1. Each one is **defined by a measurement of a
rendered note**, not by a mood, so the same value means the same thing on the
YM2612, the OPL2 and the NES — and a compiler that gets it wrong shows up as
a number, not as an argument.

| dial | 0 | 1 | measured as |
|---|---|---|---|
| `brightness` | dark, near a sine | harsh, buzzy | spectral centroid / fundamental, 1x → 0, 16x → 1 (log) |
| `attack` | instant | slow swell | 10–90 % rise of the loudness envelope, 0 ms → 0, 400 ms → 1 (log) |
| `body` | dies away | holds | level 0.6 s into the note vs the peak, −40 dB → 0, 0 dB → 1 |
| `decay` | short ring | rings for seconds | time to fall 63 % of the way to the body, 30 ms → 0, 3 s → 1 (log) |
| `metallicity` | smooth | clangorous | inharmonicity of the partials + roughness among the upper ones |
| `detune` | one pitch | wide ensemble | depth of the 0.7–3.5 Hz beating of a held note, 4 % → 0, 44 % → 1 |
| `motion` | static | strong vibrato/tremolo | amplitude and pitch movement from 3.5 Hz up |
| `bass_weight` | thin | sub-heavy | energy ≤ 1.5·f0 vs 1.5–4.5·f0, 0.2 → 0, 100 → 1 (log) |
| `articulation` | staccato | rings after note-off | time to fall 20 dB after note-off, 20 ms → 0, 1 s → 1 (log) |
| `noise` | tonal | noise | spectral flatness of the held note |
| `punch` | none | strong attack transient | octaves between the attack's and the body's spectral centroid, 3 → 1 |

The exact constants are in `python/synthesis/descriptors.py`, and a test
keeps this table and that file from drifting apart.

### Reading a dial back

A compiler turns the dials into hardware numbers, plays a note, reads the
dials back, and corrects itself (`compile` shows "asked vs heard"). The
compiler is a good first guess and the measurement does the exactness — three
renders are usually enough. Where the chip cannot reach what you asked for the
output says so instead of pretending.

## What each chip can and cannot do

Measured (`python3 python/forge.py survey` re-measures it):

* **YM2612** — all eleven dials except `noise`. Brightness 0 → 1, bass-heavy
  sounds, bells and basses are comfortable. Integer frequency multiples mean a
  sound cannot be truly inharmonic: `metallicity` tops out near 0.4–0.6 (use
  `ch3 special` in the score for a real cluster). `detune` is a second channel.
  `motion` goes in steps (the LFO's depth table), and the chip has **one LFO
  for all six channels**.
* **OPL2** — same dials. Two operators: it reaches `brightness` ~0.75 and
  `metallicity` ~0.45, and its sounds lean sub-heavy (`bass_weight` 0.4–1.0).
  `motion` is four states (vibrato/tremolo × two depths, global).
* **NES** — no `punch`, `detune` is the second pulse channel. A pulse wave is
  always bright (`brightness` 0.7–0.9): for a dark or bass sound the compiler
  picks the **triangle** (no volume register: on or off). Volume is 4 bits,
  so a quiet second voice rounds away on quiet notes. Vibrato is kept inside
  the timer's 256-step page so it does not click (writing the high byte resets
  the pulse's phase).

## Scenarios

A scenario is a JSON file that says what to search for. Fields:
`backend` (`ym2612` | `opl2` | `nes`), `role`, `seeds`, `objective`,
`register`, `generations`, `offspring`, `keep`, `archive`, `min_distance`,
`novelty`, `seed`, `locked`, `mix`, `max_probes`, `seconds`, `prefix`, `note`.
A mistake is named by field. `python3 python/forge.py template CHIP --role bass`
prints one to edit; `check` reads it without running; ready examples are in
`python/synthesis/scenarios/`.

```json
{"format": "chipgen-forge-scenario/1", "backend": "ym2612", "role": "lead",
 "seeds": ["saw_lead", {"archetype": "brass", "dna": {"brightness": 0.6}}],
 "objective": {"target": {"brightness": 0.7, "detune": 0.3},
               "box": {"metallicity": [0.0, 0.3]},
               "weights": {"brightness": 1.5}, "ignore": ["punch"]},
 "register": ["A", 4], "generations": 5, "offspring": 6, "keep": 4}
```

* `target` names the dials that matter; **dials it does not name are free**.
* `box` gives a range instead of a point; `ignore` drops a dial from scoring.
* `seeds` start the search: an archetype name, `{"archetype", "dna", "style"}`,
  `{"dna": {...}}`, `{"patch": "organ"}` (a built-in patch, measured and
  lifted into dial space), or `{"bank": "x.bank.json"}`.
* `mix` runs recipe trees (below) after the search and adds them to the bank.

### The loop

`seed → mutate → render probe → validate → score → retain`.

* **mutate** moves one to three dials of a parent (never all of them), flips a
  structural choice (algorithm, ratio set, waveform, channel), crosses two
  parents, or follows a direction a director proposed. It inherits the parent's
  learned correction, so children usually land near their target first time.
* **render probe** plays one note through the real emulator and reads the dials
  (YM2612 ~0.2 s, OPL2 ~0.4 s, NES ~1 s; renders are cached by what they
  compiled to). Detune and motion need a note of 2.4 s, so only finalists and
  genomes that ask for them get the long probe.
* **validate** rejects silence, the wrong pitch, clipping, a click, a note that
  never ends, and a dial the compiler cannot express.
* **score** = 0.70 objective fit + 0.15 fidelity (does it sound like its own
  DNA) + 0.05 quality + a novelty bonus, all in dial space.
* **retain** keeps an archive of instruments that are *different from each
  other*, so a run returns a spread and not forty copies of one answer.

A run of 30–50 candidates is 20–60 s on the FM chips and 1–3 minutes on the
NES (pure Python). `seconds` and `max_probes` cap it.

## Using the result in a score

`--forge-bank FILE` (repeatable) installs a bank's instruments by name, for all
three chips at once:

```
inst fm0 lead_01          ; a YM2612 instrument
inst opl0 pad_02          ; an OPL2 instrument
inst nes0 nes_lead_01     ; a pulse instrument  (nes1 = the other pulse)
inst nes2 nes_bass_01     ; a triangle instrument
inst nes3 nes_hat         ; a noise instrument
```

Things the instrument brings with it, which the score does not have to repeat:

* **A detune layer** is a second channel a few cents sharp and quieter: **the
  next channel up** (fm0 → fm1, opl3 → opl4, pulse1 → pulse2). If the score
  plays that channel the layer is dropped and the render warns; give the part a
  free channel above it or set `detune 0`.
* **A channel setting** (YM2612 LFO sensitivity, OPL2 vibrato/tremolo depth) is
  sent with the instrument select, and a `pan` line before or after it keeps
  both the panning and the sensitivity (unless the `pan` gives its own AMS/PMS).
  The chips have one LFO / one depth setting each, shared by every channel.
* **NES instruments are macros**: per frame (60 Hz) volume, duty, pitch,
  arpeggio and noise period, with a loop point while the key is down and a
  release point after. The cell triggers the instrument; a noise instrument
  ignores the period in the cell.

A bank is a JSON file you may edit. It is checked on load: a number the chip
cannot take (an operator level of 999, a duty of 7) is refused with the
instrument and the field named, not written to a register to play something
else.

`forge.py export BANK DIR` writes the engine's own files (`--bank`,
`--opl-bank`, and an NES JSON) if you want the instruments in other tools.
`forge.py play BANK [NAME]` renders a note of each to audio.

A ready set of 42 (one per archetype per chip) is
`python/synthesis/banks/starter.bank.json`; names are `fm_*`, `opl_*`, `nes_*`.

## Writing an NES driver by hand

When you know exactly what the driver should do each frame, say it directly;
it is measured and filed in a bank like any other:

```
python3 python/forge.py nes-inst my_pluck --volume "15 12 9 6 4 2 1 0" \
    --duty "0 0 1 1 2" --pitch "0 | 4 8 4 0 -4 -8 -4" --arp "12 0" \
    --layer 6:0.4 --out work/forge/hand.bank.json --wav work/pluck.wav
```

A macro is one value per frame (60 Hz). `|` marks where the loop starts while
the key is down and `/` where the release starts when it comes up; with
neither, the last value is held. `--volume` and `--period` are 0–15, `--duty`
is 0–3 (12.5 / 25 / 50 / 75 %), `--pitch` is cents, `--arp` is semitones,
`--layer CENTS:VOLUME` adds a second pulse. Then `inst nes0 my_pluck`.

## Mixing

```
python3 python/forge.py mix ym2612 slap_bass archetype:bell --weights 2,1 --out work/m.bank.json
```

Two ways, `--mode auto` tries the first and falls back to the second:

* **morph** — blend the hardware numbers: same-algorithm FM patches are
  interpolated operator by operator; with different algorithms the heavier
  parent hosts the lighter one's modulators, matched by depth in the chain;
  OPL2 operator by operator; NES macros resampled. Keeps the lumpy character
  of real patches; it is played and checked, and falls back if it is silent or
  off pitch.
* **dna** — blend what the parents measure, weighted, compile afresh. Works
  across chips and never produces nonsense; keeps no detail of either parent.

A mix of mixes is a recipe tree, depth up to 6, one render per node:

```json
{"mix": [{"from": "slap_bass", "weight": 2},
         {"mix": [{"from": "archetype:bell"}, {"from": "brass"}], "weight": 1}]}
```

`from` is a bank entry, a built-in patch or `archetype:NAME`.

## Where an instrument sits

```
python3 python/forge.py place work/song.trk --bank work/lead.bank.json \
    --assign fm0=bass_01 --assign fm1=lead_01 --assign fm2=pad_01 --fit
```

It profiles each part (register, notes per bar, how long notes are held, role)
and each instrument (partials, attack, fall, sustain, release — one probe at the
part's register), builds a spectral footprint per row in 24 Bark bands, and
names what collides:

| conflict | what it means |
|---|---|
| `masking` | two parts share most of their spectrum at the same times; the quieter vanishes |
| `bass_stack` / `mud` | two parts live under D3, or a mid part puts energy under ~300 Hz |
| `sustain_blanket` | a long-held part within an octave of a busy one fills the gaps it needs |
| `onsets` | both parts attack in under 12 ms on the same rows: the hits stack |
| `clash` | notes a second, tritone or seventh apart in two metallic timbres |
| `dynamics` | a part is louder than its role allows against the lead |
| `channel` | a detune layer needs a channel another part is playing |
| `lfo` | FM parts want different LFO rates (there is one) |

`--fit` takes the worst conflict, applies its suggestion with real renders
(dial deltas, level, LFO rate), keeps it only if the whole arrangement costs
less, and repeats. The roles come from the score's shape and are soft; a part
below D3 whose role is a guess is treated as the bass; say `--roles fm0=bass`
when the shape does not.

## The model's part

Only two places, both optional (`python/synthesis/director.py`):

* **directions** — once every few generations the model sees a table of
  the best instruments' measured dials against the target and answers with a
  few `{"axis": "brightness", "add": 0.2}` moves; the search applies them to a
  share of the offspring. A wrong answer costs a few renders.
* **rank** — the last look at the finalists.

`python3 python/forge.py run SCENARIO --director heuristic|none|file:reply.json|llm`.
`llm` takes `--endpoint URL --model NAME` (any OpenAI-compatible server) and
**never stops a run**: an error, a timeout or an answer in prose falls back to
the offline director, and everything the model was asked and said is written
next to the bank. From a chat window with no API:
`forge.py director-prompt SCENARIO` prints the question; paste the model's JSON
reply into `reply.json` and run with `--director file:reply.json`.

## Extending it

* **A new family of sounds**: a scenario file. No code.
* **A new archetype**: one line in `python/synthesis/archetypes.py`; `forge.py
  survey` shows what each chip makes of it. The numbers are opinions about what
  that kind of sound measures — if a chip cannot reach them the survey marks
  the dial with `!`.
* **A new chip**: a `Backend` (`backends/base.py`): `compile`, `render`,
  `events`, `setup_events`, `install`, `to_dict`/`from_dict`, `supports`,
  `genes`. Measuring, scoring, searching, mixing and placement are chip-blind.
* **A new conflict rule**: a `_detector` in `arrangement.py` that appends
  `Conflict(kind, parts, severity, message)` and a branch in `_suggestion`.

## Limits that are measured, not guessed

* Dials interact. `brightness` and `bass_weight` pull against each other; a
  request for both extremes is answered with the best compromise and the table
  shows which dial gave way.
* `body` and `decay` are coupled: `body` is the level at 0.6 s, so a note
  that is 30 dB down by then cannot also have a long decay. The body wins.
* `detune` is read from a note that holds for a second or more: a pluck or a
  stab reads `-` (not measured), never a false zero. It also depends on the
  register: the layer is tuned for a ~1 Hz beat at the instrument's own
  register (`register` in the scenario).
* `motion` and `articulation` read `-` where the note has died before they can
  be measured. Tremolo and PWM change the loudness envelope and so move
  `attack`/`body` a little; that is the instrument, not an error.
* The NES triangle is on or off; a 4-bit mixer cannot do a quiet second voice.
* Timing: all numbers above are from this machine's emulators in pure Python;
  they depend on it.
