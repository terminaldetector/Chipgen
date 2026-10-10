"""synthesis — make new instruments and drivers, offline, from a few dials.

The engine's banks hold a fixed set of FM and OPL2 patches and no NES
instruments at all, and every one was written by a person. A model that
needs a sound the bank lacks had one option: write raw operator registers,
which is the thing the rest of chipgen exists to spare it. This package is
the other option.

    seed -> mutate -> render probe -> validate -> score -> retain

* `dna.DNA` — nine semantic dials (brightness, attack, body, decay,
  metallicity, detune, motion, bass weight, articulation) plus noise and
  punch. Each is defined by a measurement, so the same dial means the same
  thing on every chip.
* `backends` — one compiler per chip from DNA to what the hardware has:
  YM2612 operators, algorithm, feedback and envelopes; OPL2 operators;
  NES duty, volume, pitch, arpeggio and noise macros with a frame driver.
* `descriptors`, `probe`, `validate`, `score` — measurement without a
  model: a short note is rendered through the real emulator and the dials
  are read back off the audio.
* `evolve.Forge` — the loop, with an archive that keeps a spread of
  instruments and not forty copies of the best one.
* `mixing` — blend patches into new ones (recursively); `arrangement` —
  decide where in an arrangement an instrument can sit without covering
  the melody, the harmony or the bass.
* `director` — the only place a language model is asked anything, and
  only to choose a direction or judge finalists. Everything works
  without it.
* `bank`, scenarios — JSON files, so a new family of sounds is a file and
  not a code change. `placement` puts the result in a score (detune
  layers, channel settings, NES macro notes) as ordinary events.

Nothing here edits the engine. The output is a bank file; load it with
`--forge-bank` and name its instruments in the score. The command line is
`python3 python/forge.py`.
"""

import os
import sys

_PYTHON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PYTHON not in sys.path:
    sys.path.insert(0, _PYTHON)

from .dna import DNA, AXES, CORE_AXES, DNAError  # noqa: E402,F401

__all__ = ["DNA", "AXES", "CORE_AXES", "DNAError", "info"]


def info() -> dict:
    """The layer as data: for bridge/manifest.json, and for a model that
    wants the vocabulary without reading the code."""
    from . import archetypes, bank, director
    from .backends import backends, get_backend
    from .dna import AXIS_DOC
    return {
        "what": "make new instruments for the YM2612, OPL2 and NES from "
                "eleven measured dials, offline; blend existing ones; "
                "check how instruments sit together in a score",
        "dials": {axis: {"zero": doc[0], "one": doc[1], "measured": doc[2]}
                  for axis, doc in AXIS_DOC.items()},
        "chips": {name: {"chip": get_backend(name).chip,
                         "cannot_express": [a for a in AXIS_DOC
                                            if a not in get_backend(name).supports],
                         "channels": get_backend(name).channels}
                  for name in backends()},
        "archetypes": {
            n: {"roles": list(archetypes.get(n)[2]),
                "register": "%s%d" % archetypes.get(n)[1],
                "what": archetypes.get(n)[3]}
            for n in archetypes.names()},
        "scenario": {
            "format": bank.SCENARIO_FORMAT,
            "fields": sorted(bank.SCENARIO_KEYS),
            "example": {"backend": "opl2", "role": "lead",
                        "seeds": ["saw_lead"],
                        "objective": {"target": {"brightness": 0.7,
                                                 "attack": 0.05}},
                        "generations": 6, "offspring": 8, "keep": 4},
            "note": "axes a target does not name are free, not pulled to a "
                    "default",
        },
        "bank": {"format": bank.BANK_FORMAT,
                 "load": "python3 python/chipgen.py song.trk --forge-bank "
                         "work/forge/x.bank.json",
                 "in_a_score": "`inst fm0 NAME`, `inst opl0 NAME`, "
                               "`inst nes0 NAME` (pulse), `inst nes2 NAME` "
                               "(triangle), `inst nes3 NAME` (noise); a "
                               "detune layer takes the next channel up and "
                               "is dropped, with a warning, if the score "
                               "plays it"},
        "commands": {
            "dials": "python3 python/forge.py axes",
            "try one": "python3 python/forge.py compile opl2 --archetype "
                       "bell --dna brightness=0.8",
            "search": "python3 python/forge.py run work/forge/x.json "
                      "--out work/forge/x.bank.json",
            "blend": "python3 python/forge.py mix ym2612 slap_bass "
                     "archetype:bell --weights 2,1",
            "where it sits": "python3 python/forge.py place work/song.trk "
                             "--assign fm0=NAME --fit",
            "listen": "python3 python/forge.py play work/forge/x.bank.json",
            "NES by hand": "python3 python/forge.py nes-inst NAME --volume "
                           "\"15 12 | 9 / 4 0\" --duty 2 --out "
                           "work/forge/x.bank.json",
        },
        "model": {"asked": "which way to push the dials, and which "
                           "finalist to keep; never once per candidate",
                  "reply_format": "JSON: {\"directions\": [{\"axis\": "
                                  "\"brightness\", \"add\": 0.2}]}",
                  "max_directions": director.MAX_DIRECTIONS,
                  "max_step": director.MAX_ADD},
    }
