# The agentic loop: compose, hear, fix, remember, continue

Chipgen used to be a renderer an agent fed a score to. This layer makes
it the place the agent works in: the piece lives in a project folder, not
in the model's context; any range of it can be rendered with the chip's
state chased in, measured voice by voice, traced from a second of audio
back to the register writes that made it, corrected locally, compared at
matched loudness, committed or undone, and continued section by section
from what came before. What worked is kept as a measured record that the
next run can use.

    Intent -> Plan -> Compose -> Render -> Hear -> Diagnose -> Revise
           -> Remember -> Continue

One backend runs the whole loop: the Mega Drive (YM2612 through
Nuked-OPN2, SN76489). The NES, OPL2 and Schism (IT) backends say
"Unsupported" for it, with the reason, instead of degrading in silence.

The ear in every example here is the **Tool ear**: measurements of the
render. Its reports say "technically analysed the render ... nothing was
listened to", and no file in this tree claims a listening verdict. (The
tests also run the Native and Hybrid ears, with a stand-in adapter that
reads the WAV for the probes and returns a scripted report for music.)

## Using it

```bash
python3 python/agent.py create_musical_state preset=masked_lead project_id=demo
python3 python/agent.py hear_range project_id=demo range=all
python3 python/agent.py propose_patch project_id=demo observation_id=o1
python3 python/agent.py improve project_id=demo observation_id=o1
python3 python/agent.py compare_versions project_id=demo rev_a=0 rev_b=1
python3 python/agent.py rollback_patch project_id=demo revision_id=0

python3 python/agent.py create_musical_state preset=etude project_id=e
python3 python/agent.py continue_composition project_id=e seed=7 correct=true
python3 python/agent.py export project_id=e format=vgm
```

From Python: `Agent(root).call(op, **args)` returns the same dicts. Every
reply is compact JSON with the ids to ask about next and its cost
(renders, seconds, the reply's own size); a refusal is `{"error": code,
"message": reason}`. Projects live under `--root` (default
`work/agentic/`, inside the folder an agent's files go in), one folder
each:

    state.json        the MusicalState (atomic writes)
    revisions/        r0000.json ... one per change, never rewritten
    renders/          the render cache (float32), keyed by event hash
    out/              A/B audio, exports
    calls.jsonl       every facade call with its cost

and `memory.jsonl` beside them holds the LearningRecords of every project
under that root.

| operation | arguments | does |
|---|---|---|
| `inspect_capabilities` | target | the manifest below, and whether an audio adapter passed the probes |
| `create_musical_state` | intent, target, preset or key/bpm/plan/instruments | a project |
| `inspect_musical_state` | project_id, scope: card, observations, decisions, revisions, motifs, trk, timeline, section:ID | a short card |
| `compose_range` | project_id, section?, rows? | the agent's rows for a section (checked, committed), or the built-in composer's next section |
| `render_range` | project_id, range, stems?, fmt, rev? | MP3/WAV of a range, the mix and each voice |
| `hear_range` | project_id, range, mode auto/tool/native/hybrid, focus? | observations, stored with ids |
| `trace` | project_id, t0, t1, voice? | notes sounding, source lines, instrument, decoded register writes |
| `diagnose_range` | project_id, observation_id | the diagnostic card |
| `propose_patch` | project_id, observation_id | hypotheses with patch ids (`o1.h2`) and expected effects |
| `compare_versions` | project_id, rev_a, rev_b, range | both measured, guards of b against a, A/B loudness-matched |
| `commit_patch` | project_id, patch_id or patch | apply, measure, guard, commit — or refuse with the failed guard |
| `rollback_patch` | project_id, revision_id | a new revision equal to an old one |
| `improve` | project_id, observation_id, budget 3x2, memory | the whole correction loop |
| `continue_composition` | project_id, goal?, sections?, seed, correct? | compose the plan on from the cursor; resumable |
| `learn_recipe` | project_id, decision_id | the decision's LearningRecord |
| `load_bank` | project_id, path, kind forge/fm | a forge bank (`python/forge.py`) or an FM bank file, copied into the project and installed whenever it opens; then `commit_patch` with `{"op": "instrument", "voice": ..., "patch": ...}` puts a voice on one of its instruments |
| `export` | project_id, format | trk, vgm, wav, mp3, it; others refused by name |
| `add_reference` | project_id, path, tags, note, bank?, regions?, licence?, url? | a recording, score or module kept as a reference, its claims checked |
| `list_references` | project_id | their cards: grid, regions, claims and verdicts |
| `listen_compare` | project_id, range, references, sketches, bars?, dims?, mode | aligned fragments, the reel, observations with their basis |
| `check_listening` | — | whether an audio model passed the probes, and what follows if not |
| `design_instrument` | project_id, voice, range, goal or reference | a voice's instrument toward a sound goal, kept or rolled back |
| `save_sketch`, `section_sketches`, `list_sketches`, `choose_sketch`, `reject_sketch` | project_id, ... | the agent's drafts, chosen or rejected with the reason |
| `improve_toward_reference` | project_id, range, references, sketches?, mode | one guided round |
| `hear_continuation` | project_id, section, sketches? | a new section with the previous one, the join and the sketches |
| `continue_composition` | ... `listen=true`, `sketches` | each new section also heard that way |

## Audit: capability, adapter, agent access, test, limit

| capability (spec §) | adapter | agent access | test (`tests/test_agentic.py`) | limit |
|---|---|---|---|---|
| persistent MusicalState: identity, structure, plan, instruments with roles and registers, sections as tracker rows, order, motif graph, observations, decisions (§4) | `agentic/state.py` | `create_musical_state`, `inspect_musical_state` | `test_revisions_roll_back_exactly_and_an_interrupted_state_recovers` | one writer per project (no locking); schema v1, migrations refuse unknown formats |
| revisions, rollback, resume after an interruption (§4, §8) | `Project.commit / rollback / open` (append-only revisions, atomic state, repair to the last whole revision) | `rollback_patch` | same, and `test_an_interrupted_composition_resumes_to_the_same_music` | — |
| one clock: rows, ticks, seconds, bars, sections, slots, events, source lines (§4) | `agentic/timeline.py` over `tracker.loads(text, rows=...)` | every `range` argument (`all`, `B`, `B#2`, `B:bars 2-3`, `slot 4`, `bars 5-8`, `rows 32-63`, `t 3.2-5.0`) | `test_one_clock_from_rows_to_seconds_to_source_lines` | a range outside the piece is refused; one past its end is clamped and says `clamped` |
| range render with the chip's state initialised (§8) | `agentic/render.py` (chase: state events replayed, held notes re-keyed, 2 s pre-roll) | `render_range` | `test_a_range_render_with_the_state_chased_in_sounds_like_the_song` | level envelope within 0.01-0.04 dB per voice and 0.26 dB for the mix (mean, measured); the PSG square and noise restart their phase (waveform null +2.5..+3 dB, envelope within 0.5 dB on average); a DAC hit before the window is not replayed |
| stems and a cache (§8) | `render_range(voices=, exclude=)`, `Cache` keyed by the event list's hash | `render_range stems=true` | via tests A, D | each stem is a full render; the mix is not the sum of the stems (register writes take chip time: +2.9 dB measured) so backgrounds are rendered, never subtracted |
| causal trace `audio time -> note -> voice -> pattern -> instrument -> register writes` (§6) | `agentic/trace.py` (`TraceWriter` stands where the VGM writer would) | `trace`; the card's `chain_at_worst` | `test_a_moment_of_audio_traces_back_to_its_note_and_register_writes` | DAC bytes are counted, not listed |
| Tool ear (§5.2) | `agentic/ear.py ToolEar`: per note, the voice against everything else in its own band (0.8-6x f0, body 40-240 ms); who fills that band; notes not heard; long holds; clipping | `hear_range mode=tool` | tests A, D | a measurement, not a masking model; no click detector (it fired on every PSG square edge); thresholds are starting points (below) |
| Native ear, honest detection (§5.1) | `OpenAIAudioAdapter` (`input_audio` part), `verify` (three probes answerable only from the audio) | `hear_range mode=native`, `--audio-endpoint` | `test_an_ear_is_native_only_when_it_answers_from_the_audio` | not run against a real audio model here (none reachable from this sandbox); the plumbing is tested with a stand-in that reads the WAV |
| Hybrid ear (§5.3) | `HybridEar`: each auditory observation cross-checked (confirmed / not confirmed / not measurable) | `hear_range mode=hybrid` (auto picks it only with a verified adapter) | `test_hybrid_checks_what_was_heard_against_what_was_measured` | four kinds have a measured counterpart (buried, too_quiet, too_loud, muddy); others are "not measurable" |
| diagnosis: established cause / plausible hypothesis / auditory opinion kept apart (§6) | `agentic/diagnose.py` | `diagnose_range`, `propose_patch` | test A | hypotheses are arrangement, level and timbre; never notes, rhythm or harmony |
| patches addressed `section/bars -> voice -> instrument -> parameter -> time` with realised change and units (§10) | `agentic/patch.py`: level (FM velocity, 0.75 dB steps; PSG 2 dB; DAC volume), transpose, param (modulators, piece or range), vol, instrument | `commit_patch` | `test_a_patch_reports_what_it_realised_and_refuses_what_the_chip_cannot` | carrier TL refused (rewritten at key-on); PSG below A-2 refused |
| instruments from outside the built-in bank (forge, FM bank files) | `agentic/banks.py` (copied into the project, recorded in the structure, installed on open; a detune layer measured as part of its voice) | `load_bank`, `commit_patch` op `instrument` | `test_a_forge_instrument_plays_in_its_voice_with_its_layer` | YM2612 instruments only; the facade loads banks, the forge designs them |
| recursive local correction, 3 x 2, guards, A/B at matched loudness, commit/rollback (§10) | `agentic/loop.py` | `improve`, `compare_versions` | `test_a_local_correction_is_measured_guarded_committed_and_undone` | RMS matching, not LUFS; the guards are listed below |
| motif graph and its transformations (§7) | `agentic/motif.py` (diatonic shift, transposition, inversion, retrograde, augmentation, diminution, fragment; recognition from the rows) | `inspect_musical_state scope=motifs` | `test_motif_transformations_are_recognised_from_the_notes` | one voice per motif; a rhythm-only motif is not a kind |
| continuous composition with a sliding context: previous section, this one, the next (§7) | `agentic/compose.py` | `continue_composition`, `compose_range` | `test_a_variation_develops_the_theme_and_the_harmony_holds` (test B) | the built-in composer is plain rules; an agent's rows are checked the same way |
| incremental work, checkpoint, resume (§8) | the composition cursor, one revision and one save per section | `continue_composition sections=N` | test C | not realtime: no streamed audio, no latency claimed |
| memory of causal decisions (§11) | `agentic/memory.py` LearningRecord, recall by role | `improve memory=true`, `learn_recipe` | `test_memory_makes_a_similar_task_cheaper_and_is_checked_again` (test E) | one case, not a rule; recall needs the same kind and target role |
| backend capability: Supported/Unsupported, Native/Emulated/Approximate (§12) | `agentic/capabilities.py` | `inspect_capabilities`, `export` | `test_the_backend_limits_are_refused_by_name` (test F) | the loop is Mega Drive only |
| facade, short machine-readable errors, context economy (§13, §14) | `python/agent.py` | all | `test_the_facade_answers_with_data_and_refuses_with_a_reason` | a CLI and a class; no server |

### Contradictions found and fixed

- `README.md` listed `instruments.py` as a bank of 14 FM patches; the bank
  holds 19 (the same README says 19 further down). Fixed.
- `README.md` said the test suite is ~550 tests; it is 587 with this layer.
- `README.md` said generation is batch only. Still true of realtime audio,
  but composition is now incremental by sections with checkpoints; the
  note says both.
- A range past the end of the piece was clamped to the last row without a
  word in the first version of `timeline.parse_range`; it is refused now
  (`out_of_piece`), and a range that runs past the end says `clamped`.
- The first Tool ear measured a voice against the whole mix (broadband):
  on `fixtures.masked_lead` it read -5.8 dB for the lead whether the bass
  sat in the lead's octave or an octave below, so a register fix was
  invisible to it. The in-band margin replaced it; the broadband margins
  are still reported as evidence.

## What existed, what was fixed, what was added, what remains

| | path | what |
|---|---|---|
| existed, reused | `python/tracker.py` | the notation; `loads` now also reports the row clock (`rows=`) |
| | `python/sequencer.py` | the render; `_run(writer=...)` carries the register trace |
| | `python/levels.py` | `isolate` / `_voice_of`: which event belongs to which voice |
| | `python/synthesis/phases.py` | `in_context`: attack and body margins (reported as evidence) |
| | `python/synthesis/improve.py` | the 3 x 2 budget and the commit/rollback idea, for instruments |
| | `python/harmony.py` | `label` / `strong`: the harmony read back from the notes |
| | `python/analysis.py` | the pure-Python FFT and window |
| | `python/sanity.py` | DAC sample lengths; the FM6/DAC masking measurement |
| | `python/opn2.py`, `python/instruments.py` | the velocity curve, the carriers |
| | `python/chipgen.py`, `mp3.py`, `wavio.py` | export and the A/B files |
| | `python/llm.py` | the endpoint the audio adapter calls |
| fixed | `python/tracker.py` | `loads(text, rows=...)` records each grid row's line, tick and first event (the event list is unchanged); re-pointed after the forge's placement pass |
| | `python/sequencer.py` | `reapply` writes the effect state when the chip still holds an offset (a delayed vibrato no longer leaves notes detuned) |
| | `python/synthesis/placement.py` | the placement pass keeps a score's own Wait boundaries: it merged the Waits of empty rows, and once any program was installed that moved the arpeggio steps and vibrato ticks of every score |
| | `python/agentic/loop.py`, `compose.py`, `agent.py` | `improve` and `continue_composition` took a Tool ear whatever ear was chosen; they take the chosen one now, its listening verdict recorded apart and able to veto |
| | `python/synthesis/backends/rp2a03.py` | the NES probe read a render's bytes as the pure-Python buffer does (`.data[0::2]`); with numpy installed that is a 2-D memoryview and raised. It reads the left channel through `analysis.split_stereo` now. Found by a reviewer who ran the suite with numpy; the whole suite now runs both ways |
| | `README.md` | stale numbers and the batch note (above) |
| added | `python/agentic/state.py` | MusicalState, project folder, revisions, rollback, repair, migrations |
| | `python/agentic/timeline.py` | the one clock, ranges, notes with source lines |
| | `python/agentic/render.py` | chased range renders, stems, cache, chase error |
| | `python/agentic/trace.py` | register trace and the causal chain |
| | `python/agentic/ear.py` | Tool, Native (probe-verified), Hybrid ears |
| | `python/agentic/diagnose.py` | locate, causes with status, hypotheses with expected effects |
| | `python/agentic/patch.py` | level, transpose, param, vol with realised reports |
| | `python/agentic/loop.py` | the correction loop, guards, A/B |
| | `python/agentic/motif.py` | motifs, transformations, recognition, the graph |
| | `python/agentic/compose.py` | built-in composer, context window, transitions, harmony check, the composition loop |
| | `python/agentic/memory.py` | LearningRecords and recall |
| | `python/agentic/capabilities.py` | the backend manifest and export |
| | `python/agentic/banks.py` | forge and FM banks in a project; a detune layer belongs to its voice |
| | `python/agentic/fixtures.py` | `masked_lead`, `masked_lead_b`, `etude` |
| | `python/agent.py` | the facade and its CLI |
| | `tests/test_agentic.py` | the spec's tests A-F and their foundations (18 tests) |
| | `python/agentic/{features,references,openmpt,listen,instrument,sketches,guided}.py` | references, parallel listening, instruments, drafts, guided rounds |
| | `tests/test_agentic_refs.py` | their tests (13) |
| | `examples/agentic/references/` | one run on the Mega Drive, made by `make_reference_run.py` |
| | `examples/agentic/` | the examples below, made by `make_examples.py` |
| remains | — | see "What it does not do" |

## How a correction is decided

1. **Observe.** The Tool ear hears the range: for each pitched voice, the
   median over its notes of its energy against everything else between
   0.8x and 6x the note's fundamental, over the note's body. A lead (or
   melody, counter) more than 3 dB under is `buried`; a note more than
   20 dB under at both attack and body is `unheard`.
2. **Locate.** The card names the voice, its instrument, the worst notes
   with their source lines, and for the worst one the register writes the
   chip received.
3. **Hypothesize.** For each voice holding at least 10% of the band (an
   *established* cause, measured): lower it (the expected gain is
   arithmetic on its share and the realised dB), move it an octave away
   unless it is melodic or would leave its declared register (expected
   gain: an *assumption*, said so), darken its loudest modulator (no
   number: not estimated); and raise the target itself (exact: its
   realised dB). Remembered recipes join them, expecting what they
   measured before.
4. **Patch, render, compare.** Each candidate is applied to a copy,
   rendered in the range only (voices it did not touch come from the
   cache) and heard again.
5. **Evaluate.** Gain of at least 0.5 dB, and every guard: no new
   clipping; every note keeps its onset and pitch class (nothing added or
   removed — the music's dissonances, syncopations, echoes and density
   stay); the melody keeps its pitches; no other voice newly buried or
   unheard; an accompanying voice stays above -9 dB in its own band; the
   bass is not put above another voice where it was not; the mix level
   moves less than 4 dB.
6. **Commit or roll back.** A candidate that reaches the goal ends the
   search; otherwise the round's best is the next round's base. The best
   is committed as a revision, the A/B is written loudness-matched, the
   decision keeps every candidate. If nothing improved, nothing is
   committed and the decision says so.
7. **Learn.** The decision becomes a LearningRecord.

In the composition loop a section is heard with the bar before it (the
join is part of what is heard) and corrected in its own rows only; if,
measured over those rows, the fault is not there, nothing is changed and
the decision says `not_needed`.

The thresholds (-3 dB buried, -20 dB unheard, -9 dB floor, 0.5 dB gain,
4 dB mix) are starting points, not results of a listening test.

## Improving by reference: listening side by side

The loop above corrects faults the Tool ear measures. This layer works
toward what other music does: the agent keeps references next to the
piece, hears the same musical moment in each, in the piece and in its own
drafts, traces a difference to the notes and instruments that make it, and
tries changes that carry the reference's principle over — never its notes.

    reference -> aligned fragments -> observations (heard / measured /
    assumed) -> trace -> hypotheses -> local render -> A/B with the
    original and the reference -> guards -> commit, or roll back as a
    sketch with its reason

| piece | what it does |
|---|---|
| `agentic/features.py` | one scale for every fragment: BS.1770 loudness (a -20 dBFS sine reads -20.04 LUFS), band balance, spectral-flux onsets, attack and tails, stereo, a beat grid for a recording with no score (128 BPM clicks read 127.8, downbeat within 10 ms), a voice's notes (attack, brightness at the attack and in the body, release, vibrato depth, rate and delay from a pitch track: 30 cents at 6 Hz after 200 ms reads 28, 5.94, 210) |
| `agentic/references.py`, `openmpt.py` | `add_reference`: a recording (MP3 by LAME, WAV), a Chipgen score with its FM bank (installed for its render only), or a module played by libopenmpt with its row clock and cells. Kept with provenance (file, sha256, licence, URL), the dimensions it is a reference for (timbre, modulation, rhythm, arrangement, development, space), the user's note and regions. The source's claims — tempo, a voice's vibrato, its density, its pan, an echo between voices — are each checked against the audio, the voice rendered alone where it can be: confirmed, not confirmed, not measurable, with the reading |
| `agentic/listen.py` | the same bars from each source on its own grid (tempos and lengths kept, nothing stretched), loudness-matched, written one by one and as one reel with an index; the piece's voices alone at the mix's gain. `compare` returns observations with their basis: heard by model, measured (a reading past its threshold, ours against theirs), assumed (a link the reference's source makes plausible) — and whether anything listened |
| `agentic/instrument.py` | a voice's sound goal (vibrato, timbre and its development, attack, release) reached with a program on the voice's own patch (vibrato with its delay, a modulator-level track over the note, envelope rates) or the score's `vib` command; each candidate measured alone and in the arrangement, level-matched, guarded; an estimated second round, the families combined in a third; committed as a bank of its own, or rolled back |
| `agentic/patch.py` | articulation, density, rhythm, voicing, pan, echo — the changes a timing, line or texture problem needs; the melody is not theirs to move |
| `agentic/guided.py` | one round on a range (`improve_toward_reference`), and `hear_continuation`: a new section heard with the previous one, the join and the chosen sketches |
| `agentic/sketches.py` | the agent's drafts: other seeds or energies for a section, rejected candidates with their reason, chosen directions |

Three engine faults were found and fixed on the way, each with a test
that fails without the fix:

- a vibrato with a delay restarts at each key-on and adds nothing until
  the delay ends, but the chip kept the offset where the last note's swing
  stopped: every note after the first sat 9.3 cents sharp for the whole
  0.3 s delay (`sequencer.reapply`; 1.2 cents, the fnum step, now);
- the tracker's row clock named each row's first event by its index while
  parsing; the forge's placement pass adds events, so with a program
  instrument every later row pointed early and a range render cut by rows
  started in the wrong place (`tracker._reindex_rows`);
- the placement pass runs on every score once any program is installed,
  and it merged the Waits of empty rows. The sequencer starts its effect
  clock afresh at each Wait, so a score that never named the instrument
  had its arpeggio steps and vibrato ticks moved: a held `A-4/047` lost
  its +7 step. The pass keeps the score's own cuts of time now
  (`placement._timeline`, `_rebuild`); a score it has nothing to place in
  comes out exactly as it went in. A tracker test that left its probe
  instrument installed is how it showed: four tests in other modules
  failed after it.

What the readings can and cannot say:

- onsets per beat are counted from the score where there is one (the
  piece, a reference's source); the audio detector is set for precision
  (1.00 at 0.99 recall on a 74-onset Mega Drive mix) and finds about a
  third of a 16th-note texture's onsets (recall 0.31 and 0.38, measured),
  so a recording is compared with audio counts on both sides, and the
  count says which it is;
- a beat grid estimated from audio can lock to a dotted or half-time
  period: a score's grid that the audio confirms is kept, one it does not
  fit (a transcription's 50 ms grid: 150 BPM against the audio's 82) gives
  way to the audio's, and the record says which;
- `brightness` is the power centroid over the fundamental: comparable
  between two Mega Drive FM voices, a direction only between FM and a
  sample;
- an observation is "measured", never "heard", unless an audio model that
  passed the probes heard it — and none was reachable from this sandbox
  (`check_listening`).

## Measured

On this sandbox (pure-Python mixer, no numpy, the Nuked-OPN2 core
compiled), from `examples/agentic/make_examples.py` and the tests:

| run | candidates | renders | seconds | result |
|---|---|---|---|---|
| test A, `masked_lead`, 3 voices, 2 bars | 4 (3 + 1) | 16 | 22 | lead in its band -7.46 -> -1.28 dB: lead +3.75 dB realised (asked +5.5), pad -3.5 dB |
| test E, `masked_lead_b`, no memory (control) | 4 | 16 | 21 | -7.24 -> -2.45 dB |
| test E, `masked_lead_b`, with the record from test A | 1 | 6 | 8 | -7.24 -> -1.70 dB: the remembered chain, rendered and guarded again |
| étude, 4 sections composed, each heard and corrected before the next | 2 corrections, 1 candidate each (remembered) | 7 per hearing, 12 and 19 per correction | 118 in all | section A -3.93 -> -1.82 dB, A2 -4.08 -> -2.40 dB; B and C not buried |
| hearing a 3-bar window, 5 voices, every voice | — | 11 | about 26 | — |
| the same with the melodic voices in focus | — | 7 | 8-15 of rendering | — |
| a facade reply: `hear_range` (3 voices) / `diagnose_range` / `improve` | — | — | — | 1.0 / 6.1 / 1.7 KB, about 256 / 1520 / 430 tokens at 4 bytes each |

No model was in these loops: model tokens 0, audio-model seconds 0. A
render runs about 2.5-3x faster than real time here; a range render costs
its window plus 2 s of pre-roll and 0.5 s of tail. `examples/agentic/
measurements.json` lists every facade call of the examples with its cost.
The étude's draft (same plan and seed, no ear) is beside it for the A/B.

## What it does not do

- **It does not listen.** The Tool ear measures. Whether a fix sounds
  better is a listening question; the A/B files are there for it, matched
  in level so the louder one does not win.
- No Native ear has been run against a real audio model from here; the
  adapter and its probe are tested with a stand-in.
- The in-band margin is a heuristic over a wide band, not a masking model
  with critical bands; it misses timbre clashes that share no band energy
  and does not know about the ear's frequency sensitivity.
- No click detection; no measure of harshness, groove or phrasing.
- The correction loop (`improve`) changes arrangement, level and timbre
  only. Articulation, density, rhythm, voicing, pan and echo exist as
  patch ops and are used by the guided rounds toward a reference, with
  guards that know what each may change; orchestration (which voice plays
  what) is still the composer's.
- The built-in composer is plain rules: one motif voice, harmony per chord
  span, no modulation, rhythm templates for 16-row bars (others scaled).
  It writes a coherent étude, not good music by itself; an agent's rows
  go through the same checks.
- New instruments from nothing are the forge's job (`python/forge.py`,
  `bridge/SYNTHESIS.md`); `design_instrument` changes what a voice's own
  patch does in time (programs) toward a measured goal, it does not
  synthesise a new patch. Only YM2612 forge instruments play on a Mega
  Drive project; a detune layer needs the next FM channel free of notes,
  or the placement pass drops it.
- Recall matches by role, patch and starting margin. A record is one
  measured case in one arrangement; it is re-rendered and re-guarded
  before it is kept, and never declared a rule.
- The loop runs on the Mega Drive only. NES, OPL2 and Schism render in
  their own layers; this loop is not ported to them. What a port needs,
  engine by engine, is proposed in `bridge/AGENTIC_PORTING.md` — a
  proposal, not a port.
- Not realtime: composition is incremental by sections with checkpoints;
  nothing streams audio and no latency is claimed.
- A render costs 1-2.5 s for a few seconds of music here, and every stem
  is a full render; a hearing of many voices over a long window is slow.
- One process per project: there is no locking.
