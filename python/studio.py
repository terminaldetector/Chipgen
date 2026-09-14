"""studio.py — everything a user interface needs, derived from the engine.

## Why this exists

A graphical front end and a command-line engine are two zones of one
project, and the boundary between them is the only thing that decides
whether they can be developed apart. Put it in the wrong place and the
interface starts carrying its own copy of what the hardware does: its own
list of directives, its own descriptions of what 25% duty sounds like,
its own table of which chip has how many channels. Every one of those
copies is correct on the day it is written and wrong the first time the
engine changes, with nothing to say so.

So the boundary is here, and it runs one way: **the engine owns the
facts, the interface owns the presentation.** This module emits every
fact an interface renders, as one JSON document. A tab listing NES
directives is then a loop over `directives`, not a hand-maintained array;
a panel showing the channel bus is a loop over `voices`. An interface
built this way cannot drift, because there is nothing in it to drift.

What stays on the interface side: layout, colour, the waveform display,
the audio element, which tab is open. None of that is the engine's
business and none of it is in here.

## What is deliberately NOT derived

The preset library — the reference prompts — is authored content, not a
measurement. It ships as `studio/presets.json` and is passed through
unchanged. Someone writing a new preset should not have to touch Python,
and the engine has no opinion about what makes a good prompt.

## Stability

This is a contract, so it is versioned. `schema` goes up when a field
changes meaning or disappears; adding a field does not move it, because
an interface reading a field it already knows is unaffected by a new one
appearing beside it.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

#: Bumped when an existing field changes meaning or goes away. Adding
#: fields does not move it.
SCHEMA = 1

#: Authored content, not measurement — see the module docstring.
PRESETS_PATH = os.path.join(os.path.dirname(_HERE), "studio", "presets.json")


# --------------------------------------------------------------------------
# The channel bus: what an interface draws as a row of faders
# --------------------------------------------------------------------------
def voices() -> list:
    """Every addressable voice, with the column name a score uses.

    `column` is the contract that matters: it is what the interface puts
    in a generated `.trk`, and it is what the tracker parses. `role` is a
    conventional default, not a rule — the engine assigns no roles to
    channels, and an arrangement that treats FM0 as a lead is not wrong.
    """
    import tracker

    out = []
    for index in range(6):
        out.append({
            "chip": "YM2612", "column": f"fm{index}", "index": index,
            "label": f"FM {index}",
            "role": ("PCM drums (DAC takes this channel outright)"
                     if index == 5 else "FM voice"),
            "kind": "fm",
            "notes": ("bit 7 of register 0x2B hands this channel to the "
                      "sample player; the FM voice then measures 0.00000 "
                      "RMS, so six FM voices and a drum track are "
                      "mutually exclusive" if index == 5 else ""),
        })
    for index in range(3):
        out.append({
            "chip": "SN76489", "column": f"psg{index}", "index": index,
            "label": f"PSG {index}", "role": "square tone", "kind": "psg",
            "notes": ("0 is loudest and 15 is silent — the register is an "
                      "attenuator"),
        })
    out.append({
        "chip": "SN76489", "column": "noise", "index": 3, "label": "NOISE",
        "role": "white / periodic LFSR", "kind": "noise",
        "notes": ("writing the noise register resets the shift register, "
                  "so a hat re-gated every sixteenth replays the identical "
                  "waveform and stops sounding like noise"),
    })
    out.append({
        "chip": "YM2612", "column": "dac", "index": 6, "label": "DAC",
        "role": "8-bit PCM", "kind": "sample",
        "notes": ("accepts bytes at about 15,980 Hz and no faster; above "
                  "that the chip drops them, so a sample keeps its length "
                  "and loses its pitch"),
    })
    for index in range(9):
        out.append({
            "chip": "YM3812", "column": f"opl{index}", "index": index,
            "label": f"OPL {index}", "role": "2-operator FM", "kind": "opl",
            "notes": "",
        })
    nes = [("nes0", "Pulse 1", "pulse"), ("nes1", "Pulse 2", "pulse"),
           ("nes2", "Triangle", "triangle"), ("nes3", "Noise", "noise"),
           ("nes4", "DMC", "sample")]
    for index, (column, label, kind) in enumerate(nes):
        out.append({
            "chip": "RP2A03", "column": column, "index": index,
            "label": label, "kind": kind,
            "role": {"pulse": "melodic pulse", "triangle": "bass",
                     "noise": "percussion", "sample": "7-bit DAC"}[kind],
            "notes": ("no volume register at all — its output is "
                      "bit-identical at velocity 8 and velocity 127"
                      if kind == "triangle" else ""),
        })
    assert all(v["column"] in tracker.valid_columns() for v in out), \
        "studio.voices() names a column the tracker will not accept"
    return out


# --------------------------------------------------------------------------
# Directives: the cards an interface shows with a copy button
# --------------------------------------------------------------------------
#: Every entry is (code, chip, category, what it does). The code is
#: literal — an interface copies it into a score unchanged, so anything
#: here that the tracker will not parse is a bug, and a test parses all
#: of them.
DIRECTIVES = [
    ("nes duty nes0 0", "RP2A03", "Pulse duty",
     "12.5% duty: thin and buzzy, the reediest of the four"),
    ("nes duty nes0 1", "RP2A03", "Pulse duty",
     "25% duty: hollow, the classic NES melody tone"),
    ("nes duty nes0 2", "RP2A03", "Pulse duty",
     "50% duty: a pure square. Its even harmonics cancel — measured "
     "-40.9 dB against -5.6 for the others — which is why it reads as "
     "hollow beside them"),
    ("nes duty nes0 3", "RP2A03", "Pulse duty",
     "75% duty: the SAME waveform as 25% inverted. Measured identical "
     "harmonics, differing only in phase, so two pulses on 1 and 3 give "
     "you one timbre twice"),
    ("nes sweep nes0 3 2 up", "RP2A03", "Pitch sweep",
     "the hardware's own pitch slide, upward. `up` names the PITCH, not "
     "the register — they run opposite ways. Measured at period 1, "
     "shift 3 over half a second: +2,716 cents"),
    ("nes sweep nes0 3 2 down", "RP2A03", "Pitch sweep",
     "downward, for laser and drop effects: -2,815 cents on the same "
     "settings. A downward sweep re-arms the mute that silences a pulse "
     "below about 110 Hz"),
    ("nes sweep nes0 off", "RP2A03", "Pitch sweep",
     "back to the safe default — negate set, shift zero — which is what "
     "keeps the bottom octave of the pulse channels audible at all"),
    ("ch3 special", "YM2612", "Channel 3 mode",
     "each of channel 3's four operators takes its own pitch. The FM "
     "side has no noise generator, so this is the only route to a fixed "
     "inharmonic cluster: bells, gongs, metallic percussion"),
    ("ch3op 1 A-5", "YM2612", "Channel 3 mode",
     "pitch one of those operators. The register holds a BASE frequency, "
     "not a partial: `mul` multiplies it and the algorithm decides "
     "whether the operator sounds at all"),
    ("op fm0 4 tl 12", "YM2612", "Live FM",
     "write one operator field mid-note. Streets of Rage's title theme "
     "changes Total Level 1,657 times against 6,256 key-ons — reach for "
     "this, not `alg`, when a part sounds static"),
    ("alg fm1 4 6", "YM2612", "Live FM",
     "algorithm and feedback. Which operator is a carrier depends on it, "
     "and op1 is a carrier only in algorithm 7"),
    ("op opl0 2 wave 2", "YM3812", "Live OPL2",
     "the OPL2's four waveforms, which the YM2612 has not got. Waves 2 "
     "and 3 rectify the sine, doubling its frequency — at the same "
     "written note they sound an OCTAVE UP"),
    ("alg opl0 1 5", "YM3812", "Live OPL2",
     "this chip's whole algorithm space is one bit: 0 is FM, 1 is "
     "additive. On an additive patch both operators reach the output, so "
     "attenuating one barely moves the sum"),
    ("vol dac 55", "YM2612", "Mix",
     "the drum fader. Mastering normalises PEAK and a drum is nearly all "
     "peak: measured against the calibrated bank, the DAC peaks 5-6 dB "
     "above every FM voice while sitting BELOW them in RMS"),
    ("pattern verse", "any", "Form",
     "open a named block of rows; `order verse verse*3 break` plays them "
     "in sequence. A two-minute piece written as one sheet of rows is "
     "the wrong shape for the job"),
    ("mark chorus", "any", "Form",
     "name a section boundary, which is what makes `--profile` report by "
     "name instead of by bar number"),
]

#: Cells rather than directives: what goes in a column, not on its own
#: line. Same contract — literal text an interface copies.
CELLS = [
    ("D-2", "RP2A03", "Triangle",
     "a note on nes2. Velocity is ignored: the triangle has no volume "
     "register, so its output is bit-identical at velocity 8 and 127"),
    ("4m:70", "RP2A03", "Noise",
     "period 4, `m` for the short 93-step shift register — the only "
     "route to a pitched metallic ring — at velocity 70. The scale runs "
     "backwards: 0 is the highest pitch"),
    ("12:80", "RP2A03", "Noise",
     "period 12 on the long 32,767-step register: standard snare and "
     "explosion hiss"),
    ("kick", "RP2A03", "DMC",
     "a kit sample through $4011, a plain 7-bit DAC — the same kit the "
     "Genesis DAC uses, one bit coarser"),
    ("A-2:100/1F0", "any", "Effects",
     "note at velocity 100 sliding up. `4xy` is speed THEN depth, the "
     "ProTracker order; `Axy` is x up, y down"),
    ("kick@D-3:0.5", "YM2612", "DAC",
     "a kit sample repitched and at half level. Pitching up thins the "
     "data rather than feeding faster, because the chip's byte ceiling "
     "is 15,980 Hz"),
]


def directives() -> list:
    out = []
    for code, chip, category, description in DIRECTIVES:
        out.append({"code": code, "chip": chip, "category": category,
                    "description": description, "kind": "directive"})
    for code, chip, category, description in CELLS:
        out.append({"code": code, "chip": chip, "category": category,
                    "description": description, "kind": "cell"})
    return out


# --------------------------------------------------------------------------
# Presets: authored, passed through
# --------------------------------------------------------------------------
def presets(path: str = PRESETS_PATH) -> list:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else data.get("presets", [])


# --------------------------------------------------------------------------
# Health: what the interface's status bar reports
# --------------------------------------------------------------------------
def health() -> dict:
    """Backend and toolchain state, plus the test count if it is known.

    The test count is read from a file rather than by running the suite:
    an interface asking "are you healthy" must not trigger two minutes of
    rendering. It is stamped by tests/run_tests.py.
    """
    import core_loader
    import audio as _audio

    status = core_loader.status()
    count = None
    stamp = os.path.join(os.path.dirname(_HERE), "tests", "last_run.json")
    try:
        with open(stamp, encoding="utf-8") as handle:
            count = json.load(handle)
    except (OSError, ValueError):
        pass
    return {
        "python": sys.version.split()[0],
        "numpy": _audio.HAVE_NUMPY,
        "scipy": _audio.HAVE_SCIPY,
        "dsp_backend": _audio.backend_name(),
        "cores": {"ym2612": status["ym2612"], "sn76489": status["sn76489"],
                  "ym3812": "pure-python", "rp2a03": "pure-python"},
        "notes": status["notes"],
        "tests": count,
    }


def manifest() -> dict:
    """The whole contract, as one document."""
    import chipgen

    engine = chipgen.info()
    return {
        "schema": SCHEMA,
        "name": "chipgen",
        "version": engine["version"],
        "summary": engine["summary"],
        "chips": engine["chips"],
        "voices": voices(),
        "directives": directives(),
        "presets": presets(),
        "instruments": engine["instruments"],
        "samples": engine["dac_samples"],
        "effects": engine["effect_column"],
        "health": health(),
        # The engine's own reference material, passed through so an
        # interface can surface it without restating it.
        "reference": {key: engine[key] for key in
                      ("form", "live_fm", "live_opl2", "ch3_special", "nes",
                       "mix_levels", "arrangement_checks",
                       "instrument_selection", "samples")
                      if key in engine},
        "example": engine["example"],
    }


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(
        description="emit the interface contract as JSON")
    parser.add_argument("--section", help="print one top-level key only")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)

    data = manifest()
    if args.section:
        if args.section not in data:
            parser.error(f"no section {args.section!r}. Have: "
                         f"{', '.join(sorted(data))}")
        data = data[args.section]
    print(json.dumps(data, indent=None if args.compact else 1,
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
