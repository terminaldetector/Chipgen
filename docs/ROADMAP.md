# Roadmap

What comes next, in the order that pays back soonest. Nothing below is
built; `docs/COVERAGE.md` says what is.

## 1. Make weaker models succeed

The project owner's observation, from using the chip sibling of this
project (Chipgen): models below a certain tier "choke" on a tool with this
much surface, and a heavier job like tracker modules should be given to the
stronger tier. So the surface a model meets has to be small, strict and
forgiving of the first try:

- a short **brief** per model family (what to write, with one complete
  example), kept under a token budget and tested against the compiler;
- `python3 -m schism check --json`: every problem as a record (line, source,
  message, fix) for a script to feed back verbatim;
- a **GBNF grammar** for `.sch`, so a local model cannot produce a line the
  compiler would reject;
- a **preview mixer in pure Python** (levels per channel, pitch of a note) so
  a model on a machine without libopenmpt or Schism can still check that
  its sound is audible and in tune;
- more "did you mean" (the compiler already offers one for a misspelt wave,
  not yet for misspelt keys or notes).

## 2. Reach real modules

- IT 2.14/2.15 sample decompression, so ordinary `.it` files can be read;
- `.xm`, `.s3m`, `.mod` readers that emit the same `Module` (a score of any
  of them becomes `.sch` text);
- stereo samples; pattern and channel names (OpenMPT extensions).

## 3. The corpora

Schism gets its own training corpora, separate from the chip corpora
(`corpus/README.md`): modules in, text scores out, audited and deduplicated
by the same discipline as Chipgen's, shipped as archives rather than
committed.

## 4. The article

After the corpora and Schism are done: a short article on the technical
part of the project, and on how generation is spread over models.

The distribution below is the project owner's working assumption, not a
measurement made here:

| job | model tier |
|---|---|
| NES tracks (Chipgen, register-level) | Sonnet- or Haiku-class models are enough |
| ordinary tracks (Chipgen, the heavier chips) | the "Astra" tier |
| Schism modules | "Astra"-like agents |
| anything below Astra | does not cope with the Chipgen tool |

"Astra" is the owner's name for a tier; this repository does not say which
model it is. The article should test the table instead of repeating it: the
same briefs to each tier, and for each, the share of scores that compile on
the first try, the rounds to a clean `check`, the warnings left, whether
every channel is audible (`levels.py`), and the time taken.
