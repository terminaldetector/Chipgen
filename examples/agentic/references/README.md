# Improving a piece by reference: one run on the Mega Drive

`make_reference_run.py` drives the whole interface through
`python/agent.py`, the way an agent calls it — nothing here was written by
hand, and **nothing was listened to**: no audio model that passes the
probes is reachable from the machine this ran on (`check_listening`
below), so every observation is measured or assumed and says which.

    python3 examples/agentic/references/make_reference_run.py \
        --module PATH/TO/SATELL.S3M      # about 8 minutes here, with numpy

The run writes into `examples/agentic/_work/reference_run/` (ignored):
the references and their excerpts are other people's music, kept for
study under their own licences, and never committed. The delivery
archive carries the run's audio next to this file, in `run/`.

## What the run did

**The piece.** An étude in A minor planned in four sections of four bars
(A statement, B variation, C development, D cadence; 140 BPM); A-C
composed by the built-in composer: revision 3, `before.mp3`.

**Two references**, each kept with where it came from, what it is a
reference for, and its source's claims checked against its audio:

| | what | for | the source's claims, checked by the audio |
|---|---|---|---|
| ref1 | Gunstar Heroes, "Military on the Max-Power": this repository's transcription (`.trk`) with its FM bank, rendered by Chipgen | timbre, modulation, rhythm | the vibratos of fm1 (the lead) and fm3 on 23% and 24% of their notes: confirmed; three voices' densities: confirmed; the score's 150 BPM grid: not confirmed — the audio's beat is 82.2 BPM (confidence 0.97), and the bars are cut on it; psg0 as an echo of fm2: not confirmed |
| ref2 | Purple Motion, "Satellite One" (S3M, played by libopenmpt) | space, arrangement | 125.3 BPM: confirmed (4 rows a beat assumed: the format stores none); ch1 left, ch2 and ch4 right by their channel pans: confirmed; three densities: confirmed; ch5's vibrato and ch5 as an echo of ch6: not confirmed |

**Two sketches** of the agent's own: B drafted at energy 0.95 (heard next
to B and the references, then rejected with the reason) and D at energy
0.9, chosen as the direction for D. The composer's energy sets the
dynamics and, above 0.7, the bass's octave leaps: the same notes, played
stronger.

**Parallel listening on B** (`listen_compare`, 7 renders, 42 s): B as a mix and each of
its voices alone, ref1's bars 1-4 (on its audio's grid, 11.7 s), ref2's
bars 3-6 (7.7 s) and the sketch of B, each cut on its own bars, nothing
stretched, all brought to -18 LUFS and written as one reel with an index.
18 observations: 16 measured, 2 assumed, none heard. Among them: the
lead has no vibrato where ref1's swings 56 cents on 60% of its notes; the
lead's body is bright (brightness 6.41 against 2.28); B is mono (side
over mid -60 dB) where ref2 is wide (-4.5 dB); ref2's texture is three
times as busy (17.97 notes a beat against 5.94). One reading was left
out and the reply says why: ref1's share of off-beat onsets — the
transcription's 52 ms rows do not divide an eighth of the 164 BPM beat it
is counted on, so the reading would measure the grid.

**A guided round on B** (`improve_toward_reference`, 308 s, 18 renders):
each gap traced to the voices that make it, hypotheses that carry the
reference's principle over, each rendered over B, measured, guarded
(nothing clips, every note keeps its row and pitch, no voice buried, no
five-note figure of a reference's melody), and written as an A/B reel
with the reference and the original:

| gap | tried | kept | measured |
|---|---|---|---|
| texture: 5.94 notes a beat against ref2's 17.97 | doubled hats | none | the busier hats bury the lead: rolled back, B stays |
| space: side over mid -60 dB against ref2's -4.49 | spread; an echo voice; both | spread + echo (the pad to one side, a quieter copy of the lead a few rows later on the other) | -60 -> -13.03 dB, 85% of the gap; spread alone 84%; the echo alone buried the lead |
| the lead's instrument: vibrato, its share of the notes and the body's brightness against ref1's lead | 6 single changes (4 vibratos, 2 modulator levels), then 3 combinations | br2 + vib1: the modulator 6 steps down for the whole note, a 56-cent vibrato at 7.7 Hz after 170 ms; the level kept by the velocity (-6.75 dB) | read back alone: a 50.8-cent swing at 7.5 Hz from 170 ms, body brightness 6.41 -> 1.95 (ref1: 2.28); 94% of the distance to the goal. The vibrato from the attack (vib4) with the same body moved the pitch centre 15 cents and was refused; with the lighter body (br1) it closed 86% |

The goal took ref1's share of swinging notes, not its 800 ms vibrato
delay: that is the length of its own held notes, longer than a beat of
the étude. The delay was fitted to B's notes from the share; all eight
of B's lead notes are long enough to swing (ref1: 60%). The instrument is
a program on the voice's own patch (`square_lead` -> `lead_d59`, a bank
of the project's own); its A/B reel plays ref1's lead alone, ours alone
before and after, and B before and after.

**The continuation.** D composed at the plan's energy, then heard with C,
the join and the chosen sketch (`hear_continuation`): the join +3.4 LU
and brighter (centroid 1436 -> 1889 Hz); the score's transition is
fine (the bass approaches the new chord by step; the lead moves up 3
semitones). The sketch's D — composed before the echo voice existed, so
that voice is silent in it — differs from the piece's D on no reading
past its threshold: it is the same notes, louder, and the comparison is
at matched loudness.

**After**: revision 6, `after.mp3`. `ab/B_before_after_reel.mp3` plays
ref1, ref2, B before and B after, aligned on bars, at one loudness. The
whole run took 462 s here (numpy installed); no model was called.

## What was not done, and what that means

**Nothing was listened to.** `check_listening` found no audio model
configured, and `check_listening.py` shows that its probes would tell
one apart: a stand-in that reads the WAV passes them, one that only
claims to hear fails. So no change above is reported as sounding better —
each is a measured move toward a reference's reading, with every guard
kept, and the A/B reels are there for a person (or a verified model) to
judge. The measurements have their own limits (`bridge/AGENTIC.md`,
"What the readings can and cannot say").

## Files (in the delivery archive, `run/`)

| file | what it is |
|---|---|
| `before.mp3`, `after.mp3`, `before.trk`, `after.trk` | the piece at revisions 3 and 6 |
| `ab/B_before_after_reel.mp3` (+ `_index.json`, each fragment alone) | ref1, ref2, B before, B after |
| `projects/piece/out/guided/<time>/listen/compare_reel.mp3` | the parallel listening of B: ours (and each voice alone), ref1, ref2, the sketch |
| `projects/piece/out/guided/<time>/L*_reel.mp3` | each hypothesis: the reference, the original, the candidate |
| `projects/piece/out/guided/<time>/instrument_lead_reel.mp3` | the instrument: ref1's lead alone, ours alone before and after, B before and after |
| `projects/piece/out/continue/D/continue_D_reel.mp3` | D, C before it, the sketch's D |
| `report.md`, `report.json` | what was observed, tried, kept and rolled back, with every reply |
| `audio_model_check.json` | the listening boundary, as the run's `check_listening` replied |
| `check_listening.json` | `check_listening.py`: the same, and the probes put to a stand-in that reads the WAV (passes) and one that only claims to hear (fails) |
| `projects/piece/state.json` | the MusicalState: decisions, sketches with their reasons, revisions |
