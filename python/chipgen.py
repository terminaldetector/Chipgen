"""
chipgen.py — the whole engine behind one function.

Everything else in python/ is the machinery; this is the front door. A
model that has just unzipped chipgen into a sandbox should not have to
learn six modules to hear a note, and a human should not have to either:

    import chipgen
    chipgen.compose(open("song.trk").read(), wav="song.wav", vgm="song.vgm")

`compose` takes tracker text, a JSON event array, a list of dicts, or a
list of Event objects, works out which it got, renders it through the real
chip emulation, and writes whatever outputs you asked for.

Also usable from a shell:

    python3 python/chipgen.py song.trk -o song.wav --vgm song.vgm
    python3 python/chipgen.py --info          # capabilities, as JSON
    python3 python/chipgen.py --demo          # render the built-in example
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import audio as _audio
import core_loader
import events as events_mod
import instruments as instruments_mod
import profile as profile_mod
import samples as samples_mod
import sanity as sanity_mod
import tracker as tracker_mod
import vgm as vgm_mod
import wavio
from events import Event
from sequencer import Sequencer

__all__ = ["compose", "Result", "info", "vocabulary", "detect_format",
           "to_events", "EXAMPLE"]

#: A complete, working score in tracker notation. Short on purpose: it is
#: what `--demo` renders and what the bridge shows a model as a starting
#: point, so it has to be readable at a glance and correct.
EXAMPLE = """\
title chipgen example
bpm 150
lpb 4
inst fm0 bass
inst fm1 square_lead
inst fm2 strings
pan fm2 C 0 2
cols fm0 fm1 fm2 psg0 noise dac

D-2  ...  D-4  D-5   ...  kick
...  ...  ...  A-5:6 ...  hat
A-2  A-4  ...  F-5   w1   snare
...  ...  ...  A-5:6 ...  hat
A#2  ...  A#3  D-5   ...  kick
...  C-5  ...  A-5:6 ...  hat
F-2  ...  ...  C-5   w1   snare
...  A-4  ===  ===   ===  hat
"""


class Result:
    """What a render produced: the audio plus where it was written."""

    __slots__ = ("it_path", "it_report",
                 "audio", "events", "sample_rate", "wav_path", "vgm_path",
                 "warnings", "source_format", "metadata")

    def __init__(self, audio, events, sample_rate, wav_path=None,
                 vgm_path=None, warnings=None, source_format="", metadata=None):
        self.audio = audio
        self.events = events
        self.sample_rate = sample_rate
        self.wav_path = wav_path
        self.vgm_path = vgm_path
        self.warnings = warnings or []
        self.source_format = source_format
        self.metadata = metadata

    @property
    def duration(self) -> float:
        return len(self.audio) / float(self.sample_rate)

    @property
    def peak(self) -> float:
        return _audio.peak(self.audio)

    def summary(self) -> str:
        parts = [f"{len(self.events)} events",
                 f"{self.duration:.2f}s",
                 f"peak {self.peak:.3f}"]
        if self.wav_path:
            parts.append(os.path.basename(self.wav_path))
        if self.vgm_path:
            parts.append(os.path.basename(self.vgm_path))
        if getattr(self, "it_path", None):
            parts.append(os.path.basename(self.it_path))
        return ", ".join(parts)

    def __repr__(self):
        return f"<chipgen.Result {self.summary()}>"

    def profile(self, bpm: float = None, beats_per_bar: int = 4):
        """RMS/peak per section — by Marker if the score used them,
        otherwise by fixed bar length if a bpm is known.

        sanity.check() (already run, its findings are in .warnings) only
        ever sees whole-track aggregates: a channel busy ANYWHERE in the
        piece clears its retrigger ceiling even if one specific section
        has a note that leaked in from the section before — a pattern
        generator's "inactive" branch writing hold instead of off is
        invisible to an event-count check but immediately obvious as a
        section that measures loud when the arrangement says it should
        have dropped to near-silence. This is the check for that: it
        looks at the rendered AUDIO, not the event list, because that bug
        class is only visible in what actually reached the speaker.
        """
        ticks_per_second = (self.metadata.ticks_per_second
                            if self.metadata else tracker_mod.DEFAULT_TICKS_PER_SECOND)
        resolved_bpm = bpm or (self.metadata.bpm if self.metadata else None)
        return profile_mod.auto_profile(self.audio, self.sample_rate, self.events,
                                        ticks_per_second, bpm=resolved_bpm,
                                        beats_per_bar=beats_per_bar)


def detect_format(source) -> str:
    """'events' | 'json' | 'tracker'."""
    if isinstance(source, (list, tuple)):
        if source and isinstance(source[0], Event):
            return "events"
        return "json"
    text = str(source).lstrip()
    if text.startswith("[") or text.startswith("{"):
        return "json"
    return "tracker"


def to_events(source, ticks_per_second: float = None):
    """Normalise any accepted input to (events, warnings, metadata).

    metadata is a tracker.Metadata when the input was tracker text (it
    carries bpm and the GD3 tags), otherwise None.
    """
    kind = detect_format(source)

    if kind == "events":
        events = list(source)
        if not events or not isinstance(events[-1], events_mod.End):
            events.append(events_mod.End())
        return events, [], None

    if kind == "json":
        data = json.loads(source) if isinstance(source, str) else list(source)
        if isinstance(data, dict):
            # tolerate {"events": [...]} — a shape models produce constantly
            data = data.get("events", data.get("pattern", []))
        events, warnings = events_mod.parse(data)
        return events, warnings, None

    events, metadata = tracker_mod.loads(str(source))
    if ticks_per_second:
        metadata.ticks_per_second = ticks_per_second
    return events, [], metadata


def compose(source, wav: str = None, vgm: str = None, tracker_out: str = None,
            it: str = None,
            bpm: float = None, ticks_per_second: float = None,
            target_rate: int = 44100, title: str = "", author: str = "",
            pal: bool = False, dc_block: bool = True,
            chip_type: str = None, bank: str = None, opl_bank: str = None,
            normalize: float = None, quiet: bool = True):
    """Render tracker text / JSON events / Event objects to audio.

    Everything but `source` is optional; with no output paths it just
    returns the audio so you can inspect or post-process it.

    `normalize` peak-normalises the result to that level (0.89 is a good
    one) as a final mastering step. None, the default, leaves levels alone
    so that two renders stay comparable — the CLI passes a value because a
    file handed to someone should be playable without reaching for the
    volume knob.

    Returns a Result. Bad-but-recoverable input (an out-of-range channel, a
    lowercase note name, a missing End) is repaired and reported in
    Result.warnings rather than raised — a model that got 95% of a pattern
    right should hear the 95%.
    """
    if bank:
        # Merged into the shared bank, so names from an imported set and the
        # built-in ones are referenced the same way in a score.
        instruments_mod.load_bank(bank)
    if opl_bank:
        import opl_instruments
        opl_instruments.load_bank(opl_bank)

    events, warnings, metadata = to_events(source, ticks_per_second)

    rate = ticks_per_second
    if rate is None:
        rate = metadata.ticks_per_second if metadata else \
            tracker_mod.DEFAULT_TICKS_PER_SECOND
    if bpm and metadata:
        metadata.bpm = bpm

    import opn2
    seq = Sequencer(ticks_per_second=rate, target_rate=target_rate, pal=pal,
                    dc_block=dc_block,
                    chip_type=chip_type or opn2.DEFAULT_CHIP_TYPE)

    tag = metadata.to_gd3() if metadata else vgm_mod.GD3()
    if title:
        tag.title = title
    if author:
        tag.author = author
    if not tag.title:
        tag.title = "chipgen"

    warnings = warnings + sanity_mod.check(events, rate)


    buf = seq.render(events, vgm_path=vgm, gd3=tag)
    if normalize:
        import mixer
        buf = mixer.normalize_peak(buf, normalize)
    if wav:
        wavio.write(wav, buf, target_rate)
    if tracker_out:
        tracker_mod.dump(events, tracker_out, metadata)
    it_report = None
    if it:
        import it_export
        it_report = it_export.export(events, it, meta=metadata,
                                     title=tag.title,
                                     message=_it_message(metadata, tag))

    result = Result(buf, events, target_rate, wav, vgm, warnings,
                    detect_format(source), metadata)
    result.it_path = it
    result.it_report = it_report
    if not quiet:
        print(result.summary())
        for warning in warnings:
            print(f"  warning: {warning}")
    return result


def _it_message(metadata, tag) -> str:
    """The song message an IT carries — provenance, for whoever opens it."""
    lines = ["Generated by chipgen (YM2612 + SN76489)."]
    if tag.author:
        lines.append(f"Author: {tag.author}")
    if metadata is not None and getattr(metadata, "notes", ""):
        lines.append(metadata.notes)
    lines.append("Instruments are the emulated chips rendered to samples, "
                 "so they are close to the original near the reference "
                 "octave and drift from it further away.")
    return "\r".join(lines)


def vocabulary() -> dict:
    """Every event type, field, default and range."""
    return events_mod.describe_vocabulary()


def info() -> dict:
    """Machine-readable description of what this copy of chipgen can do.

    bridge/manifest.json is a dump of exactly this, so a model that
    unzipped the archive and a model that only got the manifest are
    looking at the same thing.
    """
    backend = core_loader.status()
    return {
        "name": "chipgen",
        "version": VERSION,
        "summary": "Generative chiptune on real YM2612 + SN76489 emulation, "
                   "driven by a flat event vocabulary any model can emit.",
        "chips": {
            "YM2612": {
                "channels": 6,
                "role": "4-operator FM; channel 6 doubles as an 8-bit PCM DAC",
                "backend": backend["ym2612"],
                "revisions": {
                    "ym2612": "discrete (Model 1 / Model 2 VA2) — has the DAC "
                              "ladder, so muting bleeds and silence is gritty",
                    "ym3438": "integrated ASIC (later models) — clean muting",
                },
            },
            "SN76489": {
                "channels": "3 tone + 1 noise",
                "role": "Sega PSG, register-level model",
                "backend": backend["sn76489"],
            },
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "numpy": _audio.HAVE_NUMPY,
            "scipy": _audio.HAVE_SCIPY,
            "dsp_backend": _audio.backend_name(),
            "notes": backend["notes"],
        },
        "inputs": {
            "tracker": "compact text grid; see python/tracker.py docstring",
            "json": "array of event objects; see events",
            "events": "python objects from events.py",
        },
        "instrument_import": {
            "tool": "python/vgm_import.py",
            "summary": "extract FM patches from any Genesis VGM into a bank "
                       "JSON, loudness-levelled against the built-in bank",
            "usage": "python3 python/vgm_import.py song.vgm -o bank.json, "
                     "then render with --bank bank.json",
        },
        "outputs": {
            "wav": "16-bit PCM, any sample rate (default 44100)",
            "vgm": "VGM 1.71 register log; plays in VGM players, imports "
                   "into DefleMask and Furnace. .vgz gzips it.",
            "tracker": "the score written back out as text",
        },
        "instruments": instruments_mod.describe(),
        "dac_samples": samples_mod.names(),
        "events": events_mod.describe_vocabulary(),
        "tracker_syntax": _tracker_syntax(),
        "example": EXAMPLE,
    }


VERSION = "0.2.0"


def _tracker_syntax() -> dict:
    return {
        "directives": sorted(tracker_mod.DIRECTIVES),
        "columns": list(tracker_mod._FM_COLUMNS + tracker_mod._PSG_COLUMNS
                        + ("noise", "dac")),
        "default_columns": list(tracker_mod.DEFAULT_COLUMNS),
        "cells": {
            "fm": "A-2 | A#3 | A-2:100 (velocity) | === (note off) | ... (hold)",
            "psg": "A-4 | A-4:8 (volume 0-15, 0 = loudest) | === | ...",
            "noise": "w0-w3 white | p0-p3 periodic | === | ...",
            "dac": ", ".join(samples_mod.names()) + " | ...",
        },
        "comments": "; anywhere, or # at line start / after whitespace",
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _master_peak(value):
    """CLI masters by default; --peak 0 opts out."""
    import mixer
    if value is None:
        return mixer.DEFAULT_MASTER_PEAK
    return value if value > 0 else None


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog="chipgen",
        description="Render a chipgen score (tracker text or JSON events) "
                    "to WAV and/or VGM.")
    parser.add_argument("source", nargs="?",
                        help="score file; '-' reads stdin")
    parser.add_argument("-o", "--wav", help="write a WAV here")
    parser.add_argument("--vgm", help="write a VGM here (.vgz to compress)")
    parser.add_argument("--tracker", help="write the score back as text here")
    parser.add_argument("--it", metavar="SONG.IT",
                        help="write an Impulse Tracker module here — opens "
                             "in Schism Tracker and OpenMPT, so the track "
                             "can be edited by hand after it is generated")
    parser.add_argument("--rate", type=int, default=44100, help="WAV sample rate")
    parser.add_argument("--ticks", type=float, help="override ticks per second")
    parser.add_argument("--title", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--pal", action="store_true", help="PAL clocks")
    parser.add_argument("--peak", type=float, default=None,
                        help="master the WAV to this peak level "
                             "(default 0.89; --peak 0 to leave levels alone)")
    parser.add_argument("--bank", metavar="BANK.JSON",
                        help="load extra instruments (see vgm_import.py)")
    parser.add_argument("--opl-bank", metavar="BANK.JSON",
                        help="load extra OPL2 patches (see opl_import.py)")
    parser.add_argument("--chip", default=None, choices=("ym2612", "ym3438"),
                        help="ym2612 = discrete Model 1 (DAC ladder, gritty); "
                             "ym3438 = later ASIC (clean). Default ym2612.")
    parser.add_argument("--no-dc-block", action="store_true",
                        help="keep the DAC ladder's DC offset instead of "
                             "centring the mix (for comparing against an "
                             "unfiltered capture)")
    parser.add_argument("--profile", action="store_true",
                        help="print RMS/peak per section after rendering — "
                             "by Marker if the score has them, else by bar "
                             "(needs a known bpm either way). Catches a "
                             "section that measures loud when the "
                             "arrangement says it should be quiet, which "
                             "sanity.py's whole-track view cannot see")
    parser.add_argument("--beats-per-bar", type=int, default=4,
                        help="for --profile's bar fallback when the score "
                             "has no Marker events (default 4)")
    parser.add_argument("--info", action="store_true",
                        help="print capabilities as JSON and exit")
    parser.add_argument("--demo", action="store_true",
                        help="render the built-in example score")
    args = parser.parse_args(argv)

    if args.info:
        print(json.dumps(info(), indent=2))
        return 0

    if args.demo:
        source = EXAMPLE
        args.wav = args.wav or "output/chipgen_demo.wav"
        args.vgm = args.vgm or "output/chipgen_demo.vgm"
    elif args.source == "-":
        source = sys.stdin.read()
    elif args.source:
        with open(args.source, encoding="utf-8") as fh:
            source = fh.read()
    else:
        parser.error("give a score file, or --demo, or --info")
        return 2

    if not (args.wav or args.vgm or args.tracker or args.it):
        args.wav = "output/chipgen.wav"

    result = compose(source, wav=args.wav, vgm=args.vgm, it=args.it,
                     opl_bank=args.opl_bank,
                     tracker_out=args.tracker, ticks_per_second=args.ticks,
                     target_rate=args.rate, title=args.title,
                     author=args.author, pal=args.pal,
                     dc_block=not args.no_dc_block,
                     chip_type=args.chip, bank=args.bank,
                     normalize=_master_peak(args.peak), quiet=False)
    for path in (result.wav_path, result.vgm_path, args.tracker, args.it):
        if path:
            print(f"wrote {path}")

    if args.profile:
        stats = result.profile(beats_per_bar=args.beats_per_bar)
        if stats:
            print()
            print(profile_mod.format_table(stats))
            # Only meaningful once the sections have actually been measured,
            # which is why it lives here rather than in compose()'s warnings.
            for warning in sanity_mod.check_sections(stats):
                print(f"\nwarning: {warning}")
        else:
            print("\n--profile: no Marker pairs and no bpm known — "
                 "nothing to segment by")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
