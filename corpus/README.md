# Corpora (not built yet)

Schism gets its own training corpora, apart from Chipgen's chip corpora.
Nothing is in this directory yet except this note.

## The plan

1. **Collect** modules the owner supplies (`.it` first; `.xm`, `.s3m` and
   `.mod` once their readers exist).
2. **Read** each with `schism/it_read.py` and write it out as `.sch` text:
   header, instrument settings by name, patterns, order. Samples are not
   copied; an instrument becomes a recipe name, or `sample=` of a named
   stand-in, so the corpus teaches arrangement and notation rather than
   embedding anyone's recordings.
3. **Audit** the way Chipgen's corpora are audited: drop duplicates
   (identical pattern data under different names), tag the platform and the
   channel count, and keep a manifest with the source and a hash for every
   entry.
4. **Ship** as archives (a zip per corpus with its manifest), not in git.

## The tools (built)

`python3 -m schism corpus index|trace|episode` (`schism/corpus.py`,
`docs/CORPUS.md`): an index with each file's hash and source record, what it
stores against what actually plays (traced in libopenmpt, jumps, breaks and
loops included), candidate windows taken only from what plays, and episodes
of 8-16 rows with the state the song is in when they start. IT, XM, S3M and
MOD alike, read-only.

## What stops it today

- Most modules found in the wild carry **compressed samples** (IT 2.14/2.15);
  the reader keeps their headers but cannot unpack the data. The pattern
  and instrument data read fine, so the text side of a corpus does not need
  the audio — but the audit's "is this module audible" step does, and a
  pass that would rewrite such a module refuses it.
- There is no `.xm`/`.s3m`/`.mod` reader for editing; `corpus` reads them
  through libopenmpt for analysis.

## Rules

- No recordings and no copyrighted music in this repository. The demos are
  synthetic, and corpus archives stay out of git.
- Every entry keeps its provenance.
