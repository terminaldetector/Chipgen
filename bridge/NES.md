# NES / Famicom protocol (RP2A03)

Companion to `CORE.md`, which covers the YM2612 and the PSG. Read that
first: the rules about verifying a render rather than trusting it apply
here unchanged. This page is only what is different about the NES, and
every number in it is output from `tests/test_nes.py` or from the core in
`python/nes_apu.py`.

The short version: this chip has five voices, one of which cannot change
volume, and they do not add up. Those two facts decide almost every
arrangement decision on the platform.

---

## 1. What you are holding

    pulse 1     $4000-$4003   4-bit volume, 4 duty cycles, sweep unit
    pulse 2     $4004-$4007   same, different sweep negation
    triangle    $4008-$400B   4-bit fixed output, NO volume control
    noise       $400C-$400F   4-bit volume, 16 fixed periods, 2 modes
    DMC         $4010-$4013   7-bit DAC / 1-bit delta samples
    $4015 channel enable    $4017 frame counter

CPU clock 1789773 Hz on NTSC, 1662607 on PAL. Native output rate here is
55930 Hz — one sample per 32 CPU cycles, which makes every internal
divider land on a whole number of steps per sample.

## 2. The triangle has no volume control

There is no register that attenuates it. It is on at full scale or it is
off. No envelope, no attenuator, no sweep.

    write $4008 = 0x00, 0x40, 0x7F, 0xFF   ->  identical output level

This is not a limitation to work around, it is the reason NES music
sounds the way it does. The triangle is the bass on essentially every NES
soundtrack because a voice that cannot fade is a voice you can only give
a part that never needs to fade. Do not plan a triangle pad, a triangle
swell, or a triangle harmony line that has to sit under something.

It also means **the triangle cannot be balanced against the rest of the
mix**. If it is too loud, the other channels come up; there is no other
control.

## 3. The channels do not mix linearly

Two things at half volume are not one thing at full volume. The hardware
sums through a resistor ladder:

    pulse_out = 95.88 / (8128 / (pulse1 + pulse2) + 100)
    tnd_out   = 159.79 / (1 / (tri/8227 + noise/12241 + dmc/22638) + 100)

Measured consequences:

    one pulse at 15          0.14938
    two pulses at 15         0.25848      not 0.29876
    triangle alone at 15     0.24641      louder than a pulse

A second voice adds about 73% of what the first one was worth, and muting
one channel makes the others louder. Any reasoning that adds levels
together is wrong here in a way it is not wrong on the YM2612. If you
want to know what a mix does, render it.

## 4. The triangle sounds an octave below a pulse on the same timer

Its timer is clocked every CPU cycle and divides by 32; the pulses are
clocked every second cycle and divide by 16.

    timer 253 on a pulse     440.40 Hz
    timer 253 on the triangle 220.20 Hz    exactly 12.00 semitones

Copying a timer value from a pulse part to the triangle transposes it
down an octave. Use `triangle_period()` and `pulse_period()` rather than
moving raw values between channels.

## 5. The length counter is how the hardware does note-off

Writing the high byte of the timer ($4003/$400B/$400F) also loads a
length counter, and when it runs out the note stops on its own. Bit 5 of
$4000/$400C (and bit 7 of $4008) halts the counter so the note sustains.

    load 0x1F, counter running  ->  note ends at 0.246 s
    load 0x00, counter running  ->  note ends at 0.078 s
    halt bit set                ->  note sustains

A driver that never sets the halt bit gets a fixed note length whether it
wanted one or not. This is the most common way a first NES render comes
out sounding staccato for no visible reason: nothing errored, the notes
just ended.

## 6. Noise is sixteen periods, not a frequency

You pick an index 0-15 from a table, not a pitch. The mode bit changes
which bit of the 15-bit shift register feeds back, turning hiss into a
short metallic loop that is pitched enough to use as a note. The shift
register powers up as 1 and a zero register never leaves zero.

## 7. Dynamics are four bits and there is no envelope shape

Volume is 0-15, either constant or a linear decay from 15. No attack, no
sustain level, no release. Everything expressive on this platform is done
by rewriting the volume register per frame from the driver — which is
also how tremolo, fades and note attacks are faked.

## 8. The budget you are actually composing into

Two pulses, one triangle, one noise, one sample channel. That is the
whole chip.

- **Harmony is two voices.** The pulses are the only channels that can
  play a tune and change level. A triad needs a third, and the usual
  answer is a fast arpeggio on one pulse — which is why NES music
  arpeggiates so much. It is not a stylistic choice.
- **The triangle is the bass.** See section 2.
- **Percussion is the noise channel and the DMC.** There is one of each.
- Pulse periods below 8 are muted by the sweep unit, which puts a hard
  floor on how high a pulse can play.

## 9. Expansion chips

Several NES soundtracks use an expansion chip on the cartridge and are
not one chip at all: Gimmick! is the APU plus a Sunsoft 5B, Castlevania
III (JP) adds a VRC6, and so on. `vgm.detect_chips()` reports every chip
a VGM declares, and `vgm.unsupported_chips()` says which of them nothing
here can render.

Check both before transcribing. Reading only the APU from a two-chip
track silently drops whatever the expansion was carrying, which is
usually the bass and half the harmony — and the result looks like a
successful import.

At the time of writing this project has cores for SN76489, YM2612,
YM3812 and the NES APU. AY8910 and Sunsoft 5B are detected and refused,
not approximated.
