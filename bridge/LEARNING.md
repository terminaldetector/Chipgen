# Learning from the corpus without spending the context on it

chipgen ships with 79 real Mega Drive tracks transcribed into the same
notation it asks a model to write (`corpus/sector1/`). The obvious move —
hand the model the archive and say "learn the style" — does not work, and
it is worth being precise about why before describing what does.

## The arithmetic

| what | size | tokens (at 4 chars/token) |
|---|---|---|
| the full corpus, 235 scores | 5.8 MB | ~1,500,000 |
| sector 1, 79 scores | 2.4 MB | ~600,000 |
| **the measured digest** | **2.8 KB** | **~700** |
| digest + 10 four-bar excerpts | 33 KB | ~8,500 |

A 200k-token context holds thirty-one of the 235 scores. Reading them
leaves nothing to compose with, and 43% of every score is `...` padding —
the model pays per empty cell. Thirty-one arbitrary tracks is also a worse
sample than the whole set summarised: whichever soundtrack happens to sort
first dominates what gets learned.

The digest is the whole corpus, measured, at a two-thousandth of the cost.

## Making the pack

    python3 python/corpus_digest.py corpus/sector1 -o STUDY.md

`--no-excerpts` gives the 700-token statistics alone. `--bars N` sets how
much real notation is attached; four bars per soundtrack is the default
because it is enough to see parts sitting together and little enough that
ten of them fit in eight thousand tokens.

`corpus/STUDY.md` is a generated copy, so it can be read without running
anything.

## Handing it to a model

Put the digest in context BEFORE the task, not alongside it:

1. Unzip the bridge and read `START_HERE.md` — the engine, the notation,
   how to render.
2. Read `corpus/STUDY.md` — what this music actually does.
3. Then compose.

The order matters. A model that reads the digest after writing a score
treats it as a review checklist; one that reads it first treats it as the
idiom. What it should carry away is roughly:

- **FM0 is the bass.** It is the lowest voice on every track in the set,
  median D-3. FM5 appears on 37% of tracks because the DAC takes channel
  6 for drums.
- **Melodies mostly do not move.** A third of all note-to-note moves
  repeat the pitch, and most of the rest are a step of a tone or less.
  Leaps are 19%, and they are mostly octaves.
- **One to three FM voices at a time.** Five or six is 7% of rows.
- **Drums are on the quarter notes**, 21.9% of hits on beat one alone.
- **Notes are not struck at full level.** Median FM velocity is 21 of 127.
- **Vibrato is small.** Median 34 cents at 6 Hz — it colours a note, it
  does not bend it.

Every one of those is counted from the corpus, and the digest reprints
the distribution behind each so the model can see the spread rather than
just the headline.

## Checking what came out

The digest describes; `python/sanity.py` and `python/profile.py` check.
After composing, run:

    python3 python/chipgen.py score.trk -o out.wav --profile

Warnings there are about the arrangement, not the syntax: a channel that
never rests, a bass that never goes low, sections that claim to differ and
measure the same. A score that reads well against the digest and clean
against sanity is as close to the idiom as this project can currently
get you.

## What the digest cannot teach

It is statistics plus ten excerpts. It carries no sense of form — where a
section should change, how a melody develops, what makes a hook. Those
live in the scores themselves, and if a model has budget for thirty of
them it should read thirty; the digest is what to do when it does not.

It also inherits the transcription's own gaps, listed in
`corpus/README.md`: portamento and volume ramps are not recovered, and
every DAC hit reads as `kick`. A model learning drum timing from this
learns *when*, not *what*.
