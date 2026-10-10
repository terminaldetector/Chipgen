# Porting the reference-and-listening interface: Schism, NES, OPL2

**Status: a proposal. Nothing on this page is done.** The interface —
references kept with their sources checked against their audio, fragments
cut on bars and matched in loudness, observations that say whether they
were heard, measured or assumed, a voice's instrument moved toward a sound
goal, sketches, guided rounds — runs on the Mega Drive only
(`bridge/AGENTIC.md`). Each engine below is to be called ported only after
its own render of a guided round and its own listening check (a verified
audio model, or a person with the A/B files) — not because the code
compiles.

What carries over unchanged, whatever the engine:

- `features.py` (loudness, balance, onsets, beat grid, note profiles): it
  reads samples, not chips;
- `references.py` and `openmpt.py`: a reference is any recording, score or
  module, whatever the project's target;
- `listen.py`'s alignment, loudness matching, reels and observation basis;
- `sketches.py`, the copy guard, the report format.

What does not: everything that writes music for the chip — the range
render with the chip's state chased in, the stems, the instrument
programs, the patch ops — because the engines' sound is made differently,
and the differences are the point of having four.

## What each engine can do

| capability | Mega Drive (done) | Schism / IT (proposed) | NES 2A03 (proposed) | OPL2 YM3812 (proposed) |
|---|---|---|---|---|
| voices | 6 FM (4-op) + 3 square + noise; DAC on FM 6 | up to 64 sample channels; NNA lets one channel ring several notes | 2 pulse, triangle, noise, DMC | 9 FM (2-op), or 6 + 5 percussion |
| range render with state chased in | event replay, held notes re-keyed, 2 s pre-roll (envelopes within 0.05 dB of a full render) | libopenmpt can start at an order/row, but effect memory, envelopes mid-note and NNA tails are not chased: render from the pattern's start and cut; measure the error against a full render first | replay the APU register state (sweep, length counters and the triangle's linear counter are state too); measure | replay the operator registers; measure |
| stems | the voice's events kept, the rest dropped | libopenmpt's channel mute (S3M by channel volume: Scream Tracker drops a muted channel's tempo commands) — done for references here | per voice; the DMC shares the triangle and noise's DAC non-linearity, so a stem is not the voice in the mix: measure | per channel |
| change in time on a note | `program.py`: modulator level tracks, feedback, pitch, vibrato with delay, legato, gate | the instrument's own envelopes (volume, pan, pitch or filter), auto-vibrato, the effect column (H, U, J, D, G, Sxx) | macros (volume, duty, pitch, arpeggio) at the 60 Hz frame — `program.py` has these targets | a driver's per-frame writes (modulator TL, feedback); no hardware envelope beyond ADSR |
| timbre | modulator levels, feedback, algorithm, rates | the sample itself (the Schism forge resynthesises), the resonant filter and its envelope, loop points | 4 pulse duties, the triangle's fixed shape, noise period and mode | 4 waveforms (sine, half, abs, quarter), modulator level, feedback |
| space | hard L / R / C per FM channel; an echo voice on a free FM channel | pan 0-64 and surround per channel and per note, panning envelopes; an echo as a delayed copy (SDx) on another channel; no reverb (only what the sample has baked in) | **mono**: no pan at all; an echo needs a free pulse (there are two) | **mono** (OPL3 adds stereo; OPL2 has none); an echo on a free channel |
| density, rhythm | rows; the cell's delay (Cxx) | rows, SDx delay, speed alternation for groove | rows on the 60 Hz frame | rows |
| what a level change is | velocity -> carrier TL (0.75 dB steps) | volume column, channel and global volume (0-64) | 4-bit volume (pulse, noise); the triangle has none — only on or off | carrier TL (0.75 dB steps), KSL |
| limits that shape the music | DAC replaces FM 6; PSG floor at A-2 | sample memory; a sample's pitch range | two pulses: an echo or a counter-line costs the harmony; the triangle cannot fade | 2 operators: thinner FM; percussion mode takes 3 channels |

## What a port needs, per engine, before it is called done

1. **A range render** whose chase error against a full render is measured
   (as `render.chase_error` does on the Mega Drive) and stated.
2. **Stems** whose relation to the mix is measured (on the Mega Drive the
   mix is not the sum of the stems: +2.9 dB on two FM voices).
3. **The patch ops the engine can express**, refusing by name the ones it
   cannot (pan on the NES and the OPL2; a triangle's volume), never
   degrading in silence.
4. **Instrument goals in the engine's own terms** (a duty macro is not a
   modulator track; an IT filter envelope is not either), with the
   realised trajectory next to the asked one, as `program.explain` does.
5. **A guided round run on a fixture of that engine**, its A/B files
   written, and a listening check of them (a verified audio model or a
   person). Until then the capability table in `agentic/capabilities.py`
   keeps saying "Unsupported" for the loop on that target.
