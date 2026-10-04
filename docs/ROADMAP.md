# Roadmap

What comes next, in the order that pays back soonest. Nothing in sections
1–4 is built; section 5 follows the forge, which is (`docs/FORGE.md`).
`docs/COVERAGE.md` says what is built and how it was checked.

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
- pattern and channel names (OpenMPT extensions). (Stereo samples are
  done.)

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

"Astra" is an OpenAI model, as the project owner says; which release is not
recorded here. The article should test the table instead of repeating it: the
same briefs to each tier, and for each, the share of scores that compile on
the first try, the rounds to a clean `check`, the warnings left, whether
every channel is audible (`levels.py`), and the time taken.

## 5. After the forge

The forge (`docs/FORGE.md`) makes instruments from a description and
repairs a module's arrangement. What it still lacks, in the order it would
give back:

- **Ears.** Everything the forge knows about a sound is a measurement. A
  listening round on the starter bank and `forge_lab.mp3`, with the dials
  each listener would change, would show which archetypes are wrong and
  which dials do not mean what their names say; the same scenarios could
  then compare the heuristic director with a model's.
- **A brief for models.** A short, token-budgeted brief for `forge=`, `dna=`
  and `bank` lines (the starter names, the eleven dials in a line each, one
  complete score), tested against the compiler like the brief of section 1,
  and the article's test applied to it: the same brief to each tier, and how
  often a model's `dna=` moved the sound where it was asked.
- **Blind dials.** An attack under 15 ms (read from the raw waveform instead
  of the smoothed envelope), body and decay measured apart, the noise of
  band-limited noise.
- **More of the file.** A forged pan envelope (a sound that moves; a
  stereo sample is done), tones as one-shots for `smp`, and a
  sample budget: the forge knows what each instrument costs in samples
  against Schism's 235 and could narrow `oct=` itself.
- **Arrangement.** Choosing *which* instrument plays a part rather than
  repairing the one it was given, and the conflicts that live in the notes
  (two parts a second apart), which need the score and not the sound.
  (`forge rearrange` is the first step: it acts on the labels, with fixed
  rules for each rung; it does not choose.)
- **The chip, checked.** The FM voices of the ladder are an approximation of
  the YM2612: the operator numbers are exact, the register-to-seconds scale
  was set by ear. Chipgen has the real emulator; the same dump rendered by
  both, and the scale fitted to it, would turn "approximately" into a number.
  Likewise the NES rung is a band-limited pulse, not the APU.
- **Stacks with two loops.** A sample has one loop, so a stack of a looped
  body and a looped tail is refused. Two loops of commensurate length
  (two tones on one pitch) could be tiled to their common period; a pad
  under a percussive body wants its slow attack baked into its part of the
  sample, not left in the one volume envelope.
