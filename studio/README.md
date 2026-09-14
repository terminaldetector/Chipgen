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
| `arrangement` | every rearrangement target, its channels, ranges and what a velocity means on each | the Rearrange tab, and saying what a target costs before you pick it |
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

## Running it

Two ways, and the page works either way — it detects which it has and
says so in its status bar rather than leaving a dead button.

**With the engine** — rendering, WAV, VGM, the section profile,
rearranging onto another chip:

    python3 python/serve.py
    # http://127.0.0.1:8765

Loopback only, and that is the intent: it has no authentication and it
writes files. It is a tool on your own machine.

**Without it** — the whole reference, no Python at all:

    python3 studio/make_bundle.py
    # dist/chipgen-studio.zip — four files, 20 KB

Unzip that onto any static host, or hand the folder to anything that
deploys one. Every directive, preset, voice, chip and note is in it. What
a static deployment cannot do is turn a score into sound, because that
needs the emulation.

The contract is baked in at build time. That is deliberate: a bundle is a
snapshot of what the engine could do on the day it was built, and it
cannot drift afterwards because there is nothing in it to drift — only a
copy that is honestly stale rather than silently wrong. Rebuild when the
engine changes.

### Deploying the pair

The split is what makes this portable. Two archives, no build step
between them:

| archive | what it is | needs |
|---|---|---|
| `dist/chipgen-studio.zip` | zone 1, 20 KB | a static host |
| `dist/chipgen-bridge.zip` | zone 2, 466 KB | python3 |

The interface can go public on its own and be genuinely useful — it is a
complete hardware reference. The engine goes wherever there is a Python
to run it. Neither needs the other to be deployed, and neither needs a
toolchain: no npm, no bundler, no `pip install`.

## Rearranging

The Rearrange tab takes the score from the Compose tab and fits it onto
another chip. It is a server route (`POST /api/arrange`) rather than
something the page does itself, because the ranges, the role classifier
and the tracker all live in the engine — and because the answer is two
things, not one: the arranged score AND the report of what it cost.

A route that returned only the score would let this page show a
rearrangement that quietly lost the counter-melody. So the report comes
back beside it, and the status line says `Fitted onto RP2A03, 4 voices
dropped` rather than a green tick. The arrangement lands in a read-only
box; **Use as score** moves it into the Compose tab, where it is an
ordinary score you can edit before you commit to rendering it.

The file picker beside the target takes a **MIDI file** as the source
instead of the editor, and that is the join with everything outside this
project: nothing here turns a recording into notes, but every tool that
does writes MIDI. The file is read in the browser and sent as base64 —
a JSON body carries no other kind of bytes — and the import's own report
(tracks, tempo, how far quantising moved a note) comes back above the
arrangement's.

## The rule, enforced

`app.js` states **no hardware fact of its own** — no chip name, no
register address, no directive syntax, no patch name in running code. A
test greps for them. It is a blunt check and that is the point: the
moment someone types `YM2612` into the interface, the suite says so,
because a fact in two places is a fact that will disagree with itself.

Deep links work: `#directives`, `#voices`, `#chips` and the rest, so
pointing someone at the NES reference does not mean telling them which
tab to press.

## The briefing

Someone with the interface presses generate; someone without it pastes a
prompt or runs the CLI. **Both get the same ask**, and that is not a
convenience — it is the thing that keeps the two zones one project.

    python3 python/chipgen.py --prompt                       the starter
    python3 python/chipgen.py --prompt --chip-target RP2A03  one chip
    python3 python/chipgen.py --prompt --describe "..."       a full request

The interface builds the same text from `manifest.prompts`, which means
the **static bundle carries it too** — a page with no Python behind it
can still brief a model correctly, because the briefing came with the
facts. A test rebuilds the interface's version from the contract and
compares it to Python's, byte for byte.

If the interface wrote its own system prompt, the two would drift: one
would teach a model that the DAC takes channel 6 outright and the other
would not mention it, and the difference would land as tracks that render
and sound wrong. There is nothing to drift, because there is only one.

### What a briefing carries

Not the notation — `START_HERE.md` is that, and a model with the archive
reads it. A briefing carries **what will go wrong**: the behaviours that
fail silently on the chip being targeted. Those lines are pulled from
`info()`, not written into a string, so correcting a measurement corrects
the briefing and adding a chip briefs it.

They are also scoped. The crowding check reads FM channels and nothing
else, so it appears in a Genesis briefing and would be a lie in a NES
one. `tests/test_studio.py` asserts every declared chip has a briefing,
that each names columns the tracker accepts, and that no lifted fact
begins mid-sentence — these values live under self-describing keys, so a
key like `the_triangle_has_no_volume` IS the subject and has to come
along when the value is lifted out.
