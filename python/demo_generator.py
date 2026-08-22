"""
demo_generator.py — a small RULE-BASED stub that emits events in the same
vocabulary a trained neural network would output. It exists to prove the
full pipeline (events -> Sequencer -> real YM2612+SN76489 audio) works
end to end, using nothing but simple Python, no ML dependency required.

>>> THIS IS THE SEAM. <<<
Swap generate_pattern() for `your_model.generate(...) -> List[Event]` and
everything downstream (Sequencer, WAV export) needs no changes at all.
Any architecture works as long as it outputs this event list: an
autoregressive transformer over a tokenized version of events.py, an RNN,
a genetic algorithm mutating patterns, an RL agent reward-shaped on
whatever "good chiptune" means to you — the interface doesn't care.
"""

from events import (Wait, FMInstrumentSelect, FMNoteOn, FMNoteOff,
                     PSGToneOn, PSGToneOff, PSGNoiseOn, PSGNoiseOff, End)

DEFAULT_BPM = 172  # matches the "digital fusion hardcore" tempo used earlier in this project


def _ticks(beats: float, ticks_per_second: float, bpm: float) -> int:
    seconds = beats * 60.0 / bpm
    return max(1, round(seconds * ticks_per_second))


def generate_pattern(ticks_per_second: float = 192.0, bars: int = 4, bpm: float = DEFAULT_BPM):
    """A minor-key riff: driving bassline, sparse distorted lead, a
    triad chord pad, PSG arpeggio + noise hats. Returns List[Event]."""
    STEP = 0.25  # a 16th note, in beats
    events = []

    events += [
        FMInstrumentSelect(channel=0, instrument="bass"),
        FMInstrumentSelect(channel=1, instrument="distorted_lead"),
        FMInstrumentSelect(channel=2, instrument="jazz_chord_pad"),
        FMInstrumentSelect(channel=3, instrument="jazz_chord_pad"),
        FMInstrumentSelect(channel=4, instrument="jazz_chord_pad"),
    ]

    bass_pattern = [("A", 2), ("A", 2), ("C", 3), ("A", 2),
                     ("G", 2), ("G", 2), ("A", 2), ("A", 2)]
    lead_pattern = [None, None, ("E", 4), None, ("C", 4), None, ("D", 4), None,
                     None, None, ("A", 3), None, None, ("G", 4), None, None]
    chord_notes = [("A", 3), ("C", 4), ("E", 4)]  # A minor triad

    for bar in range(bars):
        # chord pad: re-strike once per bar (sustained jazz-funk chord)
        for ch, (note, octv) in zip((2, 3, 4), chord_notes):
            events.append(FMNoteOn(channel=ch, note=note, octave=octv))

        for step in range(16):
            bass_note, bass_oct = bass_pattern[step % len(bass_pattern)]
            events.append(FMNoteOn(channel=0, note=bass_note, octave=bass_oct))

            lead = lead_pattern[step % len(lead_pattern)]
            if lead is not None:
                events.append(FMNoteOn(channel=1, note=lead[0], octave=lead[1]))

            # PSG: fast arpeggio on ch0, noise "hat" every other step
            arp_note, arp_oct = [("A", 4), ("C", 5), ("E", 5), ("A", 5)][step % 4]
            events.append(PSGToneOn(channel=0, note=arp_note, octave=arp_oct, volume=3))
            if step % 2 == 0:
                events.append(PSGNoiseOn(white=True, rate=1, volume=6))

            events.append(Wait(ticks=_ticks(STEP, ticks_per_second, bpm)))

            events.append(FMNoteOff(channel=0))
            if lead is not None:
                events.append(FMNoteOff(channel=1))
            events.append(PSGToneOff(channel=0))
            events.append(PSGNoiseOff())

        for ch in (2, 3, 4):
            events.append(FMNoteOff(channel=ch))

    events.append(End())
    return events
