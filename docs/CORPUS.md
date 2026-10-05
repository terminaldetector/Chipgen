# Reading a corpus of modules

A zip of modules does not teach anything by being loaded. What a small model
can use is an **index** it can search and short, exact **episodes** it can
read: 8–16 rows of one pattern, the rows around them, the state the song is
in when they start, where they come from and who wrote them. `schism corpus`
builds both for every format libopenmpt plays (IT, XM, S3M, MOD) and never
writes to the originals (a test holds that: the bytes and the modification
time are the same after indexing and reading).

```text
python3 -m schism corpus index modules/*.it modules/*.xm modules/*.s3m modules/*.mod \
    --manifest manifest.json -o index.jsonl
python3 -m schism corpus trace modules/Necros/isotoxin.s3m
python3 -m schism corpus episode modules/Necros/isotoxin.s3m --order 13 --row 0 --rows 12
```

`--manifest` maps a file name to its source record (author, origin,
licence); each entry carries it, with the file's SHA-256. The index is JSON
lines; the episode is one JSON object.

## Three things kept apart

| level | what it is | example |
|---|---|---|
| native | the cell as its own format writes it, formatted by libopenmpt (instrument and volume in hex) | `C-5 01v40 H93` |
| normalized | libopenmpt's numbers: note, instrument, volume command, effect, parameter | `effect 5, param 0x93` |
| interpretation | what a card says the cells do, with its confidence | "a delayed vibrato" |

libopenmpt's effect numbers are its own: an IT `H` is 5 and an IT `A` is 16
(measured), and by its enum an XM `4`, the same vibrato, is 5 too. `schism/corpus.py` names them (`EFFECT_NAMES`) and keeps
the IT letter of each (`IT_LETTER`), and the test writes every letter A–Z
and reads it back to hold the table. An episode gives both the native text
and the numbers with their names, and leaves interpretation to a card.

## What plays is the engine's question

A pattern can be stored and not listed; listed behind a jump and never
played; or played twice by a loop. So the index has both:

- `stored`: the order list, the non-empty patterns, the ones the list names,
  and the non-empty ones it does not (`unlisted_nonempty`);
- `played`: what libopenmpt actually passes through when it plays the song
  once (`trace`): every (order, pattern, row) with the speed and tempo
  there, in the order they play, loops included; `listed_never_played` names
  the patterns in the order list that the song jumps over.

The trace renders at 8 kHz in 4 ms steps (shorter than the shortest row a
module can have) and ends where libopenmpt says the song ends (its own
duration; it renders a few frames past it, at the position it would loop
back to, and those are not counted). A song that loops in a way the end
detection misses stops at `--limit` seconds and says `"stopped": "limit"`.

Every row in the trace carries the second it starts (`at`, at most 4 ms
early). That time is the trace's own: libopenmpt plays a tick in a whole
number of samples, so a song's clock depends a little on the rate — at
tempo 128 a tick is 156 samples at 8 kHz (19.50 ms) and 861 at 44.1 kHz
(19.52 ms), and 200 s into *Fourth Symmetriad* the two traces are 0.24 s
apart. To cut a render at a row, trace at the render's rate; `corpus ab`
does.

Candidates for reading are only ever taken from what played: windows of 16
rows in played patterns, ranked by how many kinds of effect and instrument
they use, not by how many notes they hold (a dense pattern is not a
technique). This fixes the reference corpus's `recommended_patterns`, five of
which were not in their module's order list at all (Isotoxin P41, Art of
Chrome P8, Jazz with Attitude P14, 4mat's Madness P28, Two Steps P43, as the
audit of that corpus found); a static filter by the order list was the first
step, the trace is the second.

## The state an episode starts in

An `H00` in an episode means "the vibrato I had", and the rows of the
episode do not say what that was. So an episode carries `entry_state`: the
speed and tempo when it starts, and for each channel the last instrument,
note and volume written and the last non-zero parameter of each effect,
replayed from the rows the song played before it. That record is row-level:
formats differ in which effects share a memory (IT's E/F/G with compatible
Gxx off, for one), and the record says what was last written, not which
memory a given player uses. For an IT module the episode also has the
instrument headers it uses (envelopes node by node, NNA, filter), from
`python3 -m schism explain`.

## A/B in the module itself

`schism corpus ab` takes one mechanism out of a copy of a module and
measures A against B where the song plays it, from the start of the song
(so every effect memory, tempo change and voice left sounding is the
song's own), alone and in the mix, in libopenmpt and Schism Tracker:

```text
python3 -m schism corpus ab skaven-fourth_symmetriad.it --order 22 --row 0 \
    --seconds 1.85 --channels 11 --change 'inst 10 venv nodes 3'
python3 -m schism corpus ab isotoxin.s3m --order 44 --row 0 --seconds 0.96 \
    --channels 6 --change 'cell 52 1 6 effect none' --change 'cell 52 3 6 effect none'
```

A change is a few bytes (`schism/ablate.py`), and the report lists each:
offset, old, new, why. What can be changed in place:

| change | format | bytes |
|---|---|---|
| `inst N venv\|penv\|ienv\|fenv off` | IT | the envelope's on bit (and, for a filter envelope, its filter mark) |
| `inst N ... carry\|loop\|sustain off` | IT | one flag bit |
| `inst N ... nodes K` | IT | the node count: the envelope ends at node K-1 and holds |
| `inst N nna cut\|cont\|off\|fade`, `fade K`, `filter off` | IT | the instrument header |
| `cell P R C effect none\|LETTER`, `instrument K`, `volume 0-64` | S3M | the stored byte of that cell |

What is refused, with the reason: IT pattern cells (IT packs a cell with a
per-channel memory of the last values written, so one stored command can
be later cells' command too), XM and MOD cells (not yet), a field a cell
does not store (it cannot be added without moving the pattern), a stored
S3M volume taken away (the cell's mask says it is there, and libopenmpt
reads S3M's "none", 255, as 64), an envelope cut inside its own loop.

The original bytes are never written; the report has the original's hash
before and after.

### What the players do differently (measured on the reference corpus)

- **A muted S3M channel.** libopenmpt plays an S3M as Scream Tracker 3
  did: a muted (or disabled) channel's commands are not played at all, so
  its speed, tempo and pattern-delay commands are lost and the song moves —
  in *Isotoxin*, pattern 52 came 4.6 s early by the 170th second. Schism
  Tracker still plays a disabled channel's commands. So `corpus ab`
  isolates an S3M's channels in libopenmpt by setting the others' volume to
  0 (S3M has no command that sets it back), an IT's by muting them (an IT
  muted channel's commands are played, and its Mxx would undo a volume of
  0), and in Schism Tracker by the channels' disabled bits in a copy.
- **A filter envelope switched off but still marked as a filter.** Schism
  Tracker still lowers the filter, as if the envelope stood at its middle
  value (a resonant peak near 820 Hz on *Fourth Symmetriad*'s instrument
  2); libopenmpt does not. `fenv off` clears the mark too, and then the
  two agree; `explain` warns about an instrument that has one.
- **Carry.** Both players continue a carried envelope only while the
  previous note of the same instrument is held: a key-off or a note cut
  between the notes starts it again (`tests/test_programs.py`).
- **Random variation.** IT instruments with random volume or pan
  (eighteen in *Fourth Symmetriad*) make a mix that is not the same twice;
  the report lists them, and the readings of a channel alone do not move
  unless it plays one.

## The shape of a module, and splits that do not leak

`schism/structure.py` keeps what a seed library needs instead of the notes:
the form (patterns lettered by content, in the order they play, with the
rows, seconds, speed and tempo of each visit), the transitions (a jump, a
break, a step back, a tempo change, a busier tail as a guessed fill), each
channel's density, instruments, written register, commands and a role
guessed by a stated rule (percussion, bass, chords / pad, melodic, sparse),
notes per row and per second, the commands by name, and the tempo map.
Note numbers are the written register, not the heard one, and a role is a
guess; each says so.

`fingerprints` cuts patterns into 8-row windows and keeps each window with
eight notes or more as row, channel and interval to its first note (the
reference corpus's own measure), and `splits` puts whole groups (an
author's compositions) into test, validation and train in an order fixed
by a hash, so the split is the same everywhere; `leaks` lists any
fingerprint found in two splits.

`examples/reference/pack.py CORPUS_ZIP OUT_DIR` builds all of it for the
reference corpus: the original zip copied unchanged (hash checked), the
index, the three kinds of recommendation side by side (the corpus's own,
the audit's static ones, the trace's), 28 structures, 91 episodes (three
trace candidates a module and the audit's addresses, each with its split),
and the split by author. On that corpus: every hash matches the manifest,
the split is 19 / 5 / 4 modules (train / validation / test) and no
fingerprint crosses it, and the corpus's claim of 6385 fragments, 5130 of
them distinct (80.34 %), recounts as 6384 and 5129 (80.34 %).

## What this does not do

- It does not train anything, and it does not recover how a sample was made:
  a waveform does not say which synthesiser, patch or FM operators produced
  it. A recipe made after a corpus sound is an approximation and a card says
  so.
- It has no FTM or Furnace reader: NES and YM2612 macro tables are a
  different corpus from modules.
- It edits nothing. The IT reader of this project reads IT for editing; XM,
  S3M and MOD are read here through libopenmpt, for analysis only.

## Cards

A card is one technique found in a corpus, written so that a small model can
use it without the module: where it is (file, hash, address, the state on the
way in), what it is for in the music, what does it (a command, an envelope, a
sample), what should change if it is taken away, the A/B that takes away
only that, the measurement, and how sure the card is. `docs/cards/` has the
echo rebuilt on this project's own sound (`examples/echo/`), and
`docs/cards/reference/` nine techniques checked in the reference corpus's
modules themselves (`examples/reference/make.py` writes them from a run;
every hypothesis held in both players, four of them only after the
measurement corrected what was expected).
