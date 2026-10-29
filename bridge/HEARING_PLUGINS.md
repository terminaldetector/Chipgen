# Hearing plugins: Essentia in the agentic loop

Chipgen is a tool inside an agent's sandbox. The agent composes, renders, calls the hearing tools on the audio, reads structured observations, changes the score and renders again:

```
agent → Chipgen score → render (mix, voices alone) → Essentia → observations → agent → patch → render → Essentia → compare
```

This page covers:
- what exists;
- what was added;
- how to install it;
- what each tool returns;
- the hearing-driven revision;
- the tests;
- what is still missing.

LAION-CLAP is **not integrated**, by the owner's decision. The fork (`terminaldetector/CLAP`) was cloned and inspected, then left out. No code here loads it.

## 1. Audit: hearing before this change

| what | where | state |
|---|---|---|
| level, loudness (BS.1770), balance, onsets, beat grid, vibrato | `agentic/features.py` (stdlib) | works; used by the ToolEar and references |
| per-voice masking, band margins, clipping | `agentic/ear.py` ToolEar | works; drives `diagnose` / `improve` |
| a model that hears audio | `ear.py` NativeEar, HybridEar | code and probes work; **no live model verified** (READINESS_RU, P0) |
| timing against the grid, swing | `agentic/timing.py` | works (stdlib onsets) |
| form from the score and the audio | `agentic/form.py` | works; audio side is a stdlib chroma, onsets, brightness and level |
| joins between sections | `agentic/joins.py` | works (score only) |
| references: decode, grid, anchors, A/B | `references.py`, `listen.py`, `anchors.py` | works |

The gaps the audit names:
- no key or scale estimate;
- no predominant pitch;
- no MFCC timbre;
- no spectral roll-off, flux or flatness;
- no log attack time;
- no novelty-based section finding;
- no section roles with evidence;
- no loop in which a measured defect drives a change that is measured again.

The stdlib measures stay: they run without numpy, and every existing test uses them. The overlap is intentional. Section similarity and onsets now exist in both `form.py` and the plugin. The plugin's versions carry more descriptors and Essentia's own algorithms.

## 2. Install (CPU only, no network at run time)

Essentia is built from the fork. On this system its master needs FFmpeg 7.1 for its audio loaders, but the system has 6.1. So it is built **without libav**, and the plugin decodes every file with the `ffmpeg` command line instead. Every analysis algorithm is present.

```bash
apt-get install -y build-essential libeigen3-dev libfftw3-dev libsamplerate0-dev \
    libtag1-dev libyaml-dev python3-dev pkg-config ffmpeg
python3 -m venv /opt/hearing-venv
/opt/hearing-venv/bin/pip install "numpy==1.26.4" six pyyaml
cd terminaldetector/essentia
/opt/hearing-venv/bin/python waf configure --build-static --with-python \
    --lightweight=libsamplerate,taglib,yaml,fftw \
    --prefix=/opt/essentia --pythondir=/opt/essentia/python
/opt/hearing-venv/bin/python waf -j4 && /opt/hearing-venv/bin/python waf install
```

Built and checked here: `essentia 2.1-beta6-dev`, fork commit 33108c7.

| setting | default |
|---|---|
| `CHIPGEN_HEARING_PYTHON` — the interpreter the plugin runs in | `/opt/hearing-venv/bin/python` if present |
| `CHIPGEN_ESSENTIA_PATH` — Essentia's Python bindings | `/opt/essentia/python` |
| `CHIPGEN_HEARING_CACHE` — results by audio hash, function and arguments | `~/.cache/chipgen-hearing` |

Chipgen itself stays pure standard library. The plugin runs in its own process, `agentic/hearing/worker.py`, launched with `-I`, a timeout and one JSON request per call.

Without the interpreter, a call returns `status: "unavailable"` and the reason. Nothing is simulated.

## 3. Tools

Every tool is in the facade (`python3 python/agent.py <op> …`) and in `agentic.hearing`. A tool takes either:
- a `file`;
- or a `project_id`. The project's range is then rendered from the top, the named voices alone, into `hearing/files/`.

| op | Essentia algorithms | returns |
|---|---|---|
| `hearing_status` | — | whether the plugin runs; its version |
| `inspect_audio` | LoudnessEBUR128 | length, peak, RMS, L/R correlation, LUFS |
| `analyze_rhythm` | OnsetRate, RhythmExtractor2013 (multifeature) | see below |
| `analyze_tonality` | HPCP, KeyExtractor ×3 profiles, Key, PredominantPitchMelodia | see below |
| `analyze_timbre` | Centroid, RollOff, Flux, Flatness, HFC, MFCC, EnergyBand ×4, RMS, DynamicComplexity, LoudnessEBUR128, Envelope + LogAttackTime | see below |
| `analyze_sections` | MFCC, HPCP, Centroid, OnsetRate | see below |
| `compare_sections` | as above, on two files | spread of density, level and brightness across sections, against a reference; roles the candidate lacks |
| `evaluate_revision` | the analyses on two renders | every reading's change; the target's check |
| `hearing_revise` | all of the above | one render → hear → correct → re-hear round (§5) |

What the main analyses return:

- **`analyze_rhythm`**
  - onsets and the inter-onset intervals in beats: histogram, entropy, dominant share;
  - position of onsets in the beat, with level, and the syncopated share;
  - deviation from the grid, in ms;
  - bar-to-bar pattern overlap;
  - density per window, and where it switches;
  - the tempo estimate, with Essentia's own confidence scale.

  For a project, the grid is the score's tempo: Chipgen knows it, while the estimate can be off by an octave. The estimate is still reported.
- **`analyze_tonality`**
  - pitch-class profile (C first) and the tonal centroid (Harte 2006);
  - key estimates under bgate, temperley and edma, and whether they agree;
  - chroma and key per window, and how far the chroma moves;
  - range of the predominant pitch.
- **`analyze_timbre`**
  - spectral statistics, MFCC mean and band shares;
  - dynamics and LUFS;
  - median attack in ms.
- **`analyze_sections`**
  - sections cut at given boundaries (a project's own section starts) or at novelty peaks (a Foote kernel over MFCC and HPCP);
  - per section: density, level, centroid and chroma;
  - pairwise similarity, with its timbre, chroma, density and level parts;
  - letters (A, A′, B, with the thresholds of `form.py`);
  - **candidate** roles — intro, breakdown, climax, reprise, transition — each with its evidence.

### The envelope

```json
{"tool": "essentia", "function": "analyze_rhythm", "audio_file": "…", "audio_sha256": "…",
 "start_seconds": 0.0, "end_seconds": 12.8,
 "observations": {"measured": {}, "estimated": {}, "candidates": []},
 "confidence": null, "limitations": ["…"], "model_or_algorithm": "essentia 2.1-beta6-dev",
 "status": "ok", "elapsed_s": 1.1, "cached": false}
```

The observation fields mean different things:
- `measured` — read off the signal;
- `estimated` — a model's guess: tempo, beats, key, predominant pitch. Each carries the algorithm's own strength or confidence, and its scale;
- `candidates` — roles inferred from the measurements, with `confidence: null`. Nothing here is calibrated against a listener.

The top-level `confidence` is always `null`.

Interpretations and proposed edits are not part of the envelope. They appear only in `hearing_revise`'s report, under `findings` and `attempts`.

## 4. What the score adds that the audio cannot

The audio and the score are read together. For Chipgen's own material, a voice is **rendered alone**, not separated from the mix. Each finding carries the score's side next to the audio's:
- the voice's note count;
- the row gaps between its onsets;
- its register.

That is how a discrepancy shows. In the bass example below, Essentia finds more onsets after the change (2.0 → 2.4 per beat) than the score has notes (64 before and after): a re-keyed FM note gives the detector a second attack.

Sections are cut where the score's sections start. The tempo grid is the score's own.

## 5. Hearing-driven revision: `hearing_revise`

`agentic/hearing/revise.py` runs one round:

1. Render the mix, plus every accompanying voice and the lead alone.
2. Analyse them with Essentia.
3. Run the detectors.
4. Try the first finding's corrections in turn, using the existing patch ops on the MusicalState.
5. Re-render and re-analyse.
6. Commit the first correction that:
   - meets its **target reading**;
   - keeps the **side readings**: mix loudness within 2 LU, and the tempo estimate at the score's tempo or its octave;
   - passes Chipgen's own **guards** (`loop.guards`, with the chain's allowed changes).

If no correction qualifies, nothing is committed, and each attempt's readings are reported.

| detector | shows it | corrections | target |
|---|---|---|---|
| `monotonous_rhythm` | an accompanying voice alone: one interval ≥ 80%, entropy ≤ 0.6 bits, no syncopation | rhythm `anticipate`; rhythm `syncopate` (new: a push onto a key-off row) over the piece, then over its second half | interval entropy up ≥ 0.5 bit |
| `flat_sections` | a section planned as development, contrast, bridge or breakdown that is ≥ 0.7 alike to the statement | `density thin` in that section, voice by voice, then together | its similarity to the statement down ≥ 0.05 |
| `narrow_lead` | the lead's predominant pitch spans < 7 semitones over ≥ 8 bars | none applied: the melody is the composer's to change; a proposal is given | — |

### Executed here

**`masked_lead` ×4 (8 bars, 150 BPM), committed as revision 1:**

| | before | after |
|---|---|---|
| bass interval entropy (target) | 0.00 bits | 1.17 bits |
| bass: dominant interval share | 1.00 | 0.58 |
| bass: syncopated share | 0 | 0.38 |
| bass: distinct bar patterns | 2 | 4 |
| mix: LUFS | −20.5 | −20.3 |
| mix: tempo estimate | 150.2 | 75.0 (the score's half — reported as such) |

- Score change: 16 cells, beats 2 and 4 pushed a sixteenth early.
- Guards passed.
- The first candidate, `anticipate`, was not applicable: the bass has no held rows. That is why `syncopate` was added.

**The étude (`etude`, seed 7), `flat_sections`, rolled back.** Section B is planned as a development, but measures 0.80 alike to the statement. Thinning the bass, the pad or both left it at 0.81–0.82: the target was not met, so nothing was committed. Thinning is too weak a lever for this. Changing material, register or instrument is the composer's next move, not this loop's.

## 6. Reference profile

Sonic Superstars *Press Factory Act 2* (M4A) is **not in this session**, so it is not analysed here. Nothing about it is reported.

The same profile was run on *Delve!!!* (YM2612 + SN76489, M4A, already supplied):

```
analyze_sections min_section_s=12 → A A' A' A B A'
  0.0–43.9    A    5.5 onsets/s  −12.4 dB  2495 Hz
 43.9–107.8   A'   4.7          −11.0      3174
107.8–130.8   A'   5.4           −8.1      2488   climax candidate (level)
130.8–284.6   A    4.7          −11.3      3336   reprise candidate of 43.9 (0.97)
284.6–299.5   B    2.4          −12.7       903   breakdown + transition candidate
299.5–365.4   A'   4.1          −17.4      1658   breakdown candidate (level, centroid)
compare_sections(étude, Delve): density ratio 1.0 vs 2.3; level range 0.4 vs 10.4 dB;
  centroid ratio 1.02 vs 3.81
```

Two caveats:
- Essentia's full-band centroid and the earlier stdlib brightness do not agree on 104–128 s. The stdlib measure read it as dark; Essentia reads it as the loudest stretch. Both are reported, and neither is chosen.
- No register or channel claim is made from a compressed recording.

## 7. Tests

`tests/test_hearing.py`, 9 tests, all passing here:

```bash
python3 tests/run_tests.py test_hearing
```

| level | tests |
|---|---|
| unit, no Essentia needed | an unavailable interpreter; the cache (hit, miss on changed audio, errors not kept); target reading through lists, in both directions; the rhythm detector on given readings; `syncopate` |
| integration, real audio | a known 1 kHz tone decoded at 22.05 kHz (length, peak, centroid, band share, predominant pitch at MIDI 83.2, a window); a Chipgen bass render (32 onsets, all half-beat intervals, tempo 150, two sections lettered A A, three key profiles); the facade on a project |
| end to end | §5's bass round, committed with the readings and a decision |

The integration and end-to-end tests skip, saying why, where the interpreter is missing.

## 8. Not done, and next

- **CLAP:** not integrated (owner's decision).
- **Live listening model:** not verified (see `LIVE_EAR_RU.md`).
- **Section roles:** heuristic candidates, not calibrated.
- **`flat_sections`:** needs stronger corrections than thinning (register, instrument, material). These are the composer's.
- **Downbeat:** not estimated. Bars assume four beats from the start of the render.
- **Schism/IT renders:** the hearing tools take any file, so they work on an IT render, but `hearing_revise` is wired to Chipgen projects only.
