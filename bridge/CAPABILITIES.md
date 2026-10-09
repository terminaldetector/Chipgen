# What each engine can do, and how an agent reaches it

One row per capability: what the hardware (or the format) does, what chipgen
does with it, the command an agent uses, the test that holds it, and what is
missing. Written against this tree (`git log -1`); the YM2612, SN76489, NES
and OPL2 are emulated here, Schism/IT is the sibling project
(`claude/schism-chipgen`, `docs/CAPABILITIES.md` there).

The platform is part of the contract. A Mega Drive is six FM channels and a
PSG of three tones and a noise, and the DAC *replaces* the sixth FM channel;
the number of columns a score has is not the number of generators the chip
has. A square wave in a 16-channel IT module is not an NES; an FM voice
rendered to a sample is an IT instrument with an FM origin, not a YM2612
channel.

## YM2612 (FM, 6 channels; channel 6 is the DAC when it is on)

| engine | chipgen | agent command | test | gap |
|---|---|---|---|---|
| operator registers (TL, AR, D1R, SL, D2R, RR, MUL, DT, KS, AM, SSG) written while a note sounds | `FMOperator`; persists through a key-on (only `inst` reloads the patch) | `op fm0 2 tl 40` in a score; a program's `mod.tl` / `opN.tl` tracks and `settings` | test_livefm (a mid-note write changes the timbre), test_program (start values written at every keyed note; `priority`) | a carrier's level over time is not a program target (velocity and `vol` own it); SSG-EG is not in programs |
| algorithm and feedback (0xB0) | `FMAlgorithm` | `alg fm0 4 6`; program `fb` track or setting | test_livefm | algorithm changes are not in programs |
| one global LFO, per-channel AMS/PMS | `FMLFO`, `FMPan` | `lfo on 4`, `pan fm0 C 2 3`; the `motion` dial | test_synthesis, test_vgm | programs use the driver's vibrato (per voice, with a delay) because the LFO rate is shared by all six channels; `place` names an LFO conflict |
| key-on, key-off; a frequency write without a key-on (legato) | `FMNoteOn` always re-keys; programs do legato with Portamento | `===` ends a note; program `articulation` legato / glide / gate | test_program (one key-on per run, reset before the next, the layer follows, the gate) | the event vocabulary has no legato flag of its own |
| channel 3 special mode | `FMCh3Mode`, `FMCh3Frequency` | `ch3 special`, `ch3op 1 A-5` | test_ch3 | not a program target |
| DAC (8-bit PCM on channel 6) | `DACSample`, `DACVolume` | `dac` column | test_pcm | channel 6 is the DAC or an FM voice, not both |

## SN76489 (PSG: 3 tone, 1 noise)

| engine | chipgen | agent command | test | gap |
|---|---|---|---|---|
| 10-bit tone, 4-bit attenuation, noise (white / periodic, 4 rates) | `PSGToneOn`, `PSGVolume`, `PSGNoiseOn` | `psg0`..`psg2`, `noise` columns; the effect column | test_chips, test_effects | no forge backend and no programs |

## NES 2A03 (2 pulse, triangle, noise, DMC)

| engine | chipgen | agent command | test | gap |
|---|---|---|---|---|
| duty, 4-bit volume, sweep, 11-bit timer (the high byte resets the pulse's phase) | `NESDuty`, `NESVolume`, `NESSweep`; macros keep small pitch offsets inside the timer page | `nes duty nes0 1`, `nes sweep ...`; `forge.py nes-inst`; program `volume`/`duty`/`pitch`/`arp` tracks become the macros | test_nes, test_nes_score, test_program | legato is refused by name (every note restarts its macros); expansion chips (VRC6, N163, FDS, MMC5) are not emulated |
| triangle (no volume: on or off) | volume macro rounds to on/off | `nes2` | test_nes | a quiet triangle is impossible, and the compiler says so |
| DMC (1-bit delta PCM) | `NESSample`, `NESDMCLevel` | `nes4` | test_nes_score | — |

## OPL2 (9 channels, 2 operators, 4 waveforms)

| engine | chipgen | agent command | test | gap |
|---|---|---|---|---|
| operator registers, connection, waveform select, global vibrato/tremolo depth | `OPLOperator`, `OPLConnection`, `OPLDepth` | `op opl0 2 wave 2`; the OPL2 forge backend | test_opl | programs are not made for the OPL2 yet (a program for it is refused by name) |

## Across the chips

| what | chipgen | agent command | test | gap |
|---|---|---|---|---|
| effects at the 60 Hz frame (portamento, vibrato with a delay, tremolo, volume slide, arpeggio) | `effects.py` | the effect column, `porta`/`vib`/`trem`/`fade`/`arp` | test_effects, test_fx | — |
| the sample rate a render is written at | rendered at the chip's own rate and resampled | `--rate 48000` | test_program (header rate, and A-4 at 440 Hz at 22 050 and 48 000) | — |
| a register log a person can reopen | VGM / VGZ | `--vgm song.vgz` (opens in Furnace and DefleMask) | test_vgm | — |
| a note read phase by phase | `synthesis/phases.py` | `forge.py measure` | test_program | brightness and pitch read on the main voice; a detune layer's beat is read by the dials, not here |
| one bounded change, measured, kept or undone | `synthesis/improve.py` | `forge.py improve / commit / rollback` | test_program (budget, repeats, rollback exact, an unknown intent costs no render) | intents for the YM2612 only; no model was in the loop when it was measured, so nothing here says what a small model saves |
| a piece kept outside the model, heard, corrected in place, continued, remembered | `agentic/` (state, range render, trace, Tool/Native/Hybrid ear, diagnose, loop, compose, memory) | `python/agent.py` (see `bridge/AGENTIC.md`) | test_agentic (the spec's tests A-F) | Mega Drive only; the Tool ear measures and does not listen; the Native ear needs an adapter that passes the probes |

## Where the numbers come from

Every claim in the rows above is held by the named test or was measured for
the change that brought it (the commit says how). Things measured once and
written down: a mid-note TL write survives the next key-on (bass centroid
1938 -> 1225 -> 1129 Hz); the magnitude centroid rises when a bass's 2nd and
3rd harmonics fall 22 and 17 dB under a -40 dB DAC floor, the power centroid
falls; a program's 60 Hz writes land 2.6 ms off at 192 ticks a second and
exactly at 240.
