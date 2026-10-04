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
| native | the cell as its own format writes it, formatted by libopenmpt | `C-5 01 v64 H93` |
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
first one, the echo written into an instrument's envelopes, with its A/B in
`examples/echo/`.
