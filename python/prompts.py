"""prompts.py — how a model is briefed, in one place for both paths.

## The detail this exists for

There are two ways a model gets asked to write a track, and they must be
the same ask.

With an interface, someone fills in a chip, a tempo, a key and a
description, presses generate, and something sends that to a model. With
no interface, someone uploads the bridge zip and pastes a prompt, or runs
the CLI. If each path writes its own briefing, the two drift: the
interface teaches the model one thing about the DAC taking channel 6 and
the pasted prompt teaches it another, or teaches it nothing, and the
difference shows up as tracks that render and sound wrong.

So the briefing is assembled here, from the engine, and both paths ask
for it. `studio.manifest()["prompts"]` carries it for the interface —
including the static bundle, which has no Python behind it —
and `chipgen.py --prompt` prints it for everyone else.

## Why the constraints are generated, not written

A prompt that says "the YM2612 has six channels" is a fact in a string,
and a fact in a string is a fact nobody updates. Every hardware line
below is pulled from the same `info()` the rest of the project reads, so
adding a chip adds it to the briefing and correcting a measurement
corrects it. The prose around them is prose; the numbers are the
engine's.

## What a briefing must carry

Not the notation — `START_HERE.md` is that, and a model with the archive
reads it. What a briefing carries is **what will go wrong**: the handful
of behaviours that fail silently, where the render succeeds and the
result is not music. Those are the difference between a model that can
use this and one that produces plausible noise.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

#: Which chip a request names, and the columns a score for it uses.
CHIP_COLUMNS = {
    "YM2612": ("fm0 fm1 fm2 fm3 fm4 psg0 noise dac",
               "Sega Genesis / Mega Drive"),
    "RP2A03": ("nes0 nes1 nes2 nes3 nes4", "Nintendo NES / Famicom"),
    "YM3812": ("opl0 opl1 opl2 opl3 opl4 opl5", "AdLib / Sound Blaster PC"),
    "SN76489": ("psg0 psg1 psg2 noise", "Sega PSG alone"),
}


#: Which key of info() to lift, per chip, and the subject to put in
#: front of it. The subject matters: these values live under
#: self-describing keys, so `the_triangle_has_no_volume` reads fine as
#: JSON and begins "not approximated, refused" when lifted into a
#: sentence on its own. The key IS the subject, so it has to come along.
_FACT_SOURCES = {
    "RP2A03": [
        ("nes", "the_sweep_mute", "The pulse sweep mute"),
        ("nes", "the_triangle_has_no_volume", "The triangle has no volume"),
        ("nes", "duty_3_equals_duty_1", "Duty 3 is duty 1 inverted"),
    ],
    "YM3812": [
        ("live_opl2", "algorithm", "The algorithm space"),
        ("live_opl2", "wave_shifts_the_octave", "The waveform shifts octave"),
        ("live_opl2", "which_operator_to_attenuate",
         "Which operator to attenuate"),
        ("live_opl2", "rhythm_mode", "Rhythm mode"),
    ],
    "YM2612": [
        ("ch3_special", "what_for", "Channel 3 special mode"),
        ("mix_levels", "the_drums_own_the_master", "The drums own the master"),
        ("live_fm", "absolute", "Operator writes are absolute"),
        ("samples", "ceiling_note", "The DAC byte ceiling"),
    ],
    "SN76489": [
        ("mix_levels", "the_drums_own_the_master", "The drums own the master"),
    ],
}


def _facts_for(chip: str) -> list:
    """The things that fail silently on this chip, from the engine.

    Pulled rather than written: a briefing whose hardware notes are a
    hand-kept string is a briefing that goes stale without saying so.
    """
    import chipgen

    info = chipgen.info()
    out = []

    for section, key, subject in _FACT_SOURCES.get(chip, []):
        text = (info.get(section) or {}).get(key)
        if text:
            out.append(f"{subject}: {text}")

    if chip == "RP2A03":
        ranges = (info.get("nes") or {}).get("ranges", {})
        if ranges:
            out.append(f"Ranges — pulse: {ranges.get('pulse', '')} "
                       f"Triangle: {ranges.get('triangle', '')}")
    elif chip == "YM2612":
        ym = (info.get("chips") or {}).get("YM2612", {})
        out.insert(0, f"Voices: six FM channels, and {ym.get('role', '')}. "
                      f"So six simultaneous FM voices and a drum track are "
                      f"mutually exclusive.")
        # Crowding is an FM-channel check — sanity.py reads fm_pitches and
        # nothing else — so it belongs in an FM briefing and would be a
        # lie in a NES one.
        crowding = (info.get("arrangement_checks") or {}).get("crowding")
        if crowding:
            out.append(f"Voicing: {crowding}")

    return [line.strip() for line in out if line and line.strip()]


def starter(chip: str = None, language: str = "en") -> str:
    """The briefing that introduces the project to a model.

    Deliberately short. A model with the archive reads START_HERE.md for
    the notation; what it cannot work out from the notation is which of
    its assumptions about FM are wrong here, and that is what this says.
    """
    import chipgen

    info = chipgen.info()
    names = ", ".join(sorted(info["chips"]))
    chip = chip if chip in CHIP_COLUMNS else None

    lines = [
        f"chipgen is a chiptune engine that drives register-accurate "
        f"emulation of real sound chips: {names}. You write a score in "
        f"its tracker notation and it renders to WAV and VGM — the VGM is "
        f"a register log, so what you write is what the hardware does.",
        "",
        "1. Run `python3 bridge/bootstrap.py`. It builds the chip cores "
        "and self-tests. No network, no pip.",
        "2. Read `START_HERE.md` for the notation and `bridge/CORE.md` "
        "for the hardware. CORE.md is not optional: a session that has "
        "read only the notation writes scores that render successfully "
        "and sound wrong.",
        "3. Write the score yourself rather than generating it randomly. "
        "The point is that you are composing into the chip's registers.",
        "4. Render with "
        "`python3 python/chipgen.py song.trk -o song.wav --vgm song.vgm`.",
        "5. Check it with `--levels` and `--profile` before you call it "
        "done. A render that succeeds is not evidence that it sounds "
        "like anything.",
    ]
    if chip:
        columns, platform = CHIP_COLUMNS[chip]
        lines += ["", f"Target: {chip} ({platform}). Columns: {columns}."]
    return "\n".join(lines)


def compose(request: dict) -> str:
    """The generation request, assembled from what an interface collected.

    `request` is the interface's own fields — chip, bpm, key, style,
    bars, voices, and the free-text description. Everything else comes
    from the engine.
    """
    chip = request.get("chip") or "YM2612"
    columns, platform = CHIP_COLUMNS.get(
        chip, CHIP_COLUMNS["YM2612"])
    voices = request.get("voices")
    if voices:
        columns = " ".join(voices)

    description = (request.get("prompt") or "").strip()
    bpm = request.get("bpm") or 150
    key = request.get("key") or ""
    style = request.get("style") or ""
    bars = request.get("bars") or 8

    head = [f"Write an original chiptune score for the {chip} "
            f"({platform}) in chipgen's tracker notation."]
    if description:
        head.append("")
        head.append(f"What it should be: {description}")

    spec = [f"- tempo: {bpm} BPM"]
    if key:
        spec.append(f"- key: {key}")
    if style:
        spec.append(f"- style: {style}")
    spec.append(f"- length: {bars} bars")
    spec.append(f"- columns: `cols {columns}`")

    facts = _facts_for(chip)
    body = [
        "",
        "Specification:",
        *spec,
        "",
        "Hardware that will bite you. Every one of these fails SILENTLY —",
        "the render succeeds and the result is wrong:",
        "",
    ]
    body += [f"- {fact}" for fact in facts]
    body += [
        "",
        "Return the score as tracker notation and nothing else — no "
        "explanation around it, no markdown fence. It goes straight into "
        "the renderer.",
    ]
    return "\n".join(head + body)


def vocabulary() -> dict:
    """What an interface needs to assemble a request without inventing it.

    The templates are here rather than in the interface for the same
    reason the facts are: a briefing written in two places is written
    differently in two places.
    """
    import chipgen

    info = chipgen.info()
    return {
        "starter": starter(),
        "chips": {
            chip: {
                "platform": platform,
                "columns": columns,
                "facts": _facts_for(chip),
                "starter": starter(chip),
            }
            for chip, (columns, platform) in sorted(CHIP_COLUMNS.items())
        },
        "how": "call compose() with {chip, prompt, bpm, key, style, bars, "
               "voices} — or assemble it from `chips[chip]` if you have no "
               "Python, which is what the static bundle does",
        "no_interface": "`python3 python/chipgen.py --prompt` prints the "
                        "starter; `--prompt --chip-target RP2A03` targets "
                        "one chip. The bridge archive's bridge/PROMPT.md "
                        "is the same briefing for the upload-a-zip path.",
        "version": info["version"],
    }


def main(argv):
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="print the briefing a model is given")
    parser.add_argument("--chip", help="target one chip")
    parser.add_argument("--compose", metavar="TEXT",
                        help="assemble a full generation request around "
                             "this description")
    parser.add_argument("--bpm", type=int, default=150)
    parser.add_argument("--key", default="")
    parser.add_argument("--style", default="")
    parser.add_argument("--bars", type=int, default=8)
    parser.add_argument("--json", action="store_true",
                        help="emit the whole vocabulary instead")
    args = parser.parse_args(argv)

    if args.json:
        print(json.dumps(vocabulary(), indent=1, ensure_ascii=False))
    elif args.compose:
        print(compose({"chip": args.chip, "prompt": args.compose,
                       "bpm": args.bpm, "key": args.key,
                       "style": args.style, "bars": args.bars}))
    else:
        print(starter(args.chip))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
