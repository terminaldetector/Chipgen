# Genesis transcription corpus

235 Mega Drive tracks read out of their VGM register logs and written as
chipgen tracker notation, by `python/vgm_transcribe.py`. 379,455 notes
across 225 minutes.

The point is that a model asked to write Genesis music has almost nothing
to learn from: the material that exists is audio and register dumps, and
neither shows the arrangement. These are the same notation the model is
asked to produce.

## Sectors

Split by `python/corpus_split.py` into three, ordered by how confidently
each track transcribed and balanced so no single soundtrack dominates any
sector.

| sector | tracks | notes | size | median grid fit |
|---|---|---|---|---|
| **1 (priority)** | 79 | 90,502 | 2.4 MB | **0.68** |
| 2 | 79 | 140,499 | 3.0 MB | 0.39 |
| 3 | 77 | 148,454 | 3.4 MB | 0.32 |

`grid_fit` is the fraction of note onsets that land on the inferred tempo
grid — the transcription's own confidence. Sector 1 ships with the engine;
2 and 3 live here.

Every sector carries its own `manifest.json` with the per-track record, so
a sector read on its own still says what it contains. `full_manifest.json`
covers all 260 source tracks including the 25 that were rejected, each
with the reason.

## What is in a score, and what is not

Recovered from the register log:

- Notes, their channel, their timing.
- Velocity, estimated from carrier Total Level at key-on.
- The tempo grid, inferred — `grid_fit` says how well it fit.
- FM patches, deduplicated by timbre into a per-track bank.
- **Vibrato**, with its measured depth and speed — 4,839 spans across 163
  of the 235 tracks, a median of 11 per track. At register level it is a
  pitch deviation that keeps crossing back through zero, which is what
  separates it from a bend.

Not recovered:

- Portamento and volume ramps. Both are, at register level, the same
  stream of small writes between notes, and unlike vibrato they have no
  signature that tells them apart from ordinary retuning.
- Which drum a DAC hit was — all read as `kick`. Two hits closer together
  than the first sample's length leave no gap in the byte stream and read
  as one.

One thing worth knowing before using these as a reference for tuning:
**81 of the 235 tracks are more than 10 cents off A440**, consistently, as
a property of the game's note table rather than of the performance.
Each entry records its own `tuning_cents`.

## Provenance

Transcribed from the VGM archives uploaded to this repository. The scores
are derived from those recordings and carry whatever status the
recordings do; the manifest records which file each came from.
