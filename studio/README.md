# The two zones

The project splits in two, and the line between them is what lets either
side be worked on without breaking the other.

    ZONE 1 — interface        ZONE 2 — engine
    presentation only         every fact
    ─────────────────         ───────────────────────
    layout, colour            what the chips do
    waveform display          what a directive means
    audio element             how many channels exist
    which tab is open         what a patch measures
    the editor widget         rendering, .wav, .vgm

The rule runs one way: **the engine owns the facts, the interface owns
the presentation.** The interface never holds its own copy of a fact.

    python3 python/chipgen.py --studio        the whole contract, as JSON
    python3 python/studio.py --section voices one part of it

## Why it runs that way

An interface that keeps its own list of NES directives is correct on the
day it is written. The first time the engine gains a directive, changes
what one does, or corrects a description, the interface is wrong — and
nothing says so. It renders its stale copy confidently, and whoever
presses the copy button gets a score that does not do what the card
promised.

So the interface renders `manifest()["directives"]` rather than an array
it maintains. Same for the channel bus, the chip specifications, the
instrument list, the sample kit, the effect codes and the health line.
Adding a chip to the engine makes it appear in the interface with no
interface change at all.

## What is in the contract

| key | what it is | an interface uses it for |
|---|---|---|
| `schema` | contract version | refusing to render a manifest it does not understand |
| `chips` | every emulated chip and its specification | the architecture panel |
| `voices` | all 25 addressable voices, each with the **column** a score writes | the channel bus, and generating `.trk` |
| `directives` | every directive and cell, with literal copyable code | the reference cards |
| `presets` | the authored reference-prompt library | the preset browser |
| `instruments`, `samples`, `effects` | what a score may name | pickers and autocomplete |
| `health` | backends, toolchain, last test result | the status bar |
| `reference` | the engine's own prose on form, live FM, mix levels, NES | help text and tooltips |
| `example` | a complete working score | what goes in the editor on first load |

## The one thing that is not derived

`presets.json` is **authored content**, not measurement. The engine passes
it through unchanged and has no opinion about what makes a good prompt —
writing one should not mean touching Python.

The cost of hand-authoring is that nothing stops a typo, so the suite
stops it instead: `tests/test_studio.py` checks every preset names a chip
the engine declares and columns the tracker accepts, and it parses every
directive in the catalogue through the real parser. A card with a copy
button that produces a broken score is a failure that lands on whoever
pressed it, several steps away from the typo — so it fails here instead.

## Adding a preset

Append to `studio/presets.json`. Required: `id`, `title`, `chip`,
`prompt`, `bpm`, `voices`. Optional but worth writing: `hints`, which is
where the hardware traps for that style belong — the things that fail
silently, like the NES triangle having no volume register or the OPL2's
rectified waveforms sounding an octave up.

Run `python3 tests/run_tests.py test_studio` and it will tell you if the
preset names something that does not exist.

## Adding a chip

Nothing in the interface changes. Declare it in `info()["chips"]`, add
its voices to `studio.voices()`, and it appears in the architecture
panel and the channel bus. `test_studio.py` asserts the bus and the
tracker agree in **both** directions, so a chip reachable from a score
but missing from the bus fails the suite — an interface built on this
cannot be quietly unable to reach part of the hardware.
