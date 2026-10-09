# The agentic loop, run: what it made

Everything in this folder was made by `make_examples.py`, which drives the
loop through `python/agent.py` the way an agent would. Nothing was written
by hand, and nothing was listened to: the ear was the Tool ear, and every
verdict below is a measurement of the render.

    python3 examples/agentic/make_examples.py          # about 3.5 minutes

The audio (`*.mp3`, `etude.vgm`, and the A/B WAVs with `--wav`) is not in
the branch: renders stay out of it (`.gitignore`). The script makes them
again, and the delivery archive carries them next to these files.

## masked_lead/ — test A: one local correction

A lead (FM, square_lead, velocity 80) under a loud pad and a bass played
an octave too high, two bars (`python/agentic/fixtures.py`).

| file | what it is |
|---|---|
| `before.trk`, `after.trk` | the score before and after, in the engine's own notation |
| `hearing.json` | `hear_range`: the observation (lead buried 7.5 dB in its band) and each voice's margins |
| `diagnostic_card.json` | `diagnose_range`: where (the worst notes with their source lines; the register writes at the worst one), the causes (pad 66% and bass 34% of the lead's band: established; both within an octave: plausible), the hypotheses with their expected effects, the patches refused and why |
| `decision.json` | `improve`: every candidate of both rounds with its measurement and guards, the chain committed, the cost |
| `patch_delta.json` | the change as committed: the asked and the realised dB, every edited cell |
| `ab_A.mp3`, `ab_B.mp3` | before and after, loudness-matched (`ab_levels.json`: the original levels and the gain applied) |
| `rollback.json` | the fix undone: a new revision equal to the first, byte for byte |
| `refused_patch.json` | an agent's own "fix" — the lead an octave up — measured and refused: it moves the melody |

## memory.json — test E: the same kind of fault in another piece

`masked_lead_b` (D minor, another melody, another bass patch) corrected
twice: without memory (a control: nothing recalled, nothing learned), and
with what `masked_lead` taught. The remembered chain is tried first, and
rendered and guarded like any other candidate before it is kept.

## etude/ — a context étude

Four two-bar sections from a plan (theme, variation, development,
cadence). Each was composed from the window around it (the previous
section as written, this section's function and harmony, the next
section's first chord), heard with the bar before it, and corrected in
its own rows by the loop — with the memory — before the next section was
written. Every step is a revision.

| file | what it is |
|---|---|
| `etude.trk`, `etude.vgm`, `etude.mp3` | the étude as committed: the score, the register log the chips received (opens in Furnace and DefleMask), the audio |
| `etude_draft_no_ear.trk`, `.mp3` | the same plan and seed with no ear and no correction |
| `composition.json` | each section: revision, motif, transition, what the ear measured, each correction with its cost |
| `checks.json` | the harmony read back from the notes per chord span, the transitions, notes left hanging (none) |
| `motif_graph.json` | the motif, the instances the composer says it wrote, and those found again in the rows |
| `musical_state.json` | the whole MusicalState at the end |
| `learning_record.json` | the last LearningRecord the run wrote |

`learning_record_first.json` is the record `masked_lead` left, the one the
other runs recalled. `measurements.json` is every facade call with its
seconds, renders and the size of its reply.
