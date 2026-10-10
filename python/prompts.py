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
        f"its tracker notation and it renders to MP3, WAV and VGM — the "
        f"VGM is a register log, so what you write is what the hardware "
        f"does.",
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
        "`python3 python/chipgen.py song.trk -o song.mp3 --vgm song.vgz`. "
        "Hand back the MP3: about a tenth of a WAV, and it plays anywhere.",
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
        # The per-family briefings: self-contained, notation included.
        # `briefs[chip][profile]` has {prompt} {bpm} {bars} {rows} blanks;
        # `families` maps a model family onto a profile.
        "briefs": {chip: {profile: brief_template(chip, profile)
                          for profile in PROFILES}
                   for chip in BRIEF_CHIPS},
        "profiles": {name: spec["what"] for name, spec in PROFILES.items()},
        "families": dict(FAMILIES),
        "unknown_family": "contract",
        "no_interface": "`python3 python/chipgen.py --prompt` prints the "
                        "starter; `--prompt --chip-target RP2A03` targets "
                        "one chip. The bridge archive's bridge/PROMPT.md "
                        "is the same briefing for the upload-a-zip path.",
        "version": info["version"],
    }


# ==========================================================================
# Per-family briefings
# ==========================================================================
#
# The briefings above assume a model that reads START_HERE.md, absorbs the
# reasons, and then writes a score. Some do. Others skim, fill the gaps
# from what they know about FM synthesis in general, and — handed an
# archive full of Python — start improving the engine instead of writing
# music. Measured against this engine, the second kind fails in two ways:
#
#   * it never sees the notation at all. compose() deliberately leaves the
#     notation to START_HERE.md, so a model asked through an interface is
#     told to write "chipgen's tracker notation" and shown none of it;
#   * it reads 12,000 tokens of START_HERE.md and CORE.md, keeps the gist,
#     and writes `C-5:15` on a PSG channel (silence: it is an attenuator)
#     or an FM column with no `inst` line (seven seconds of silence, and
#     before this round, not one warning).
#
# brief() is the answer to both: self-contained, a few hundred tokens,
# rules stated as rules, every list closed, and the same rule names the
# checker reports — so what a model is told and what it is held to cannot
# drift apart.

#: How a briefing is pitched. Keyed by behaviour, not by vendor: the
#: family table below maps names onto these, and anything unrecognised
#: gets the strictest one.
PROFILES = {
    "reasoned": {
        "what": "rules with their reasons; trusts the model to generalise. "
                "For models that read a briefing and act on why",
        "reasons": True, "example_rows": 8, "repeat_contract": False,
    },
    "contract": {
        "what": "numbered rules, closed lists, a filled-in header, the "
                "output contract stated first and again last. For models "
                "that skim, or that reach for the engine's code",
        "reasons": False, "example_rows": 8, "repeat_contract": True,
    },
    "compact": {
        "what": "the smallest complete briefing: format, lists, one "
                "example, nothing else. For local models with small "
                "context windows, and for grammar-constrained decoding",
        "reasons": False, "example_rows": 4, "repeat_contract": True,
    },
}

#: Family names onto profiles. A family is a starting point, not a
#: verdict — `--profile` overrides it, and the size check below catches
#: the small member of a large family.
FAMILIES = {
    "claude": "reasoned", "sonnet": "reasoned", "opus": "reasoned",
    "haiku": "reasoned", "grok": "reasoned",
    "gpt": "contract", "chatgpt": "contract", "openai": "contract",
    "gemini": "contract", "deepseek": "contract", "mistral": "contract",
    "copilot": "contract",
    "local": "compact", "llama": "compact", "qwen": "compact",
    "phi": "compact", "gemma": "compact", "smollm": "compact",
}

#: Parameter count, in billions, at or under which a model gets the
#: compact briefing whatever its family: a small context window is the
#: binding constraint there, not the house style.
COMPACT_AT_OR_BELOW_B = 14


def profile_for(model: str = None) -> str:
    """The profile for a model name, a family name, or a profile name.

    `gpt`, `qwen2.5:7b`, `contract` and `my-local-llama-3b` all work. An
    unknown name gets "contract", because the failure this exists for is
    a model that does not read closely, and an unknown model has not
    shown that it does.
    """
    import re

    if not model:
        return "contract"
    name = model.strip().lower()
    if name in PROFILES:
        return name
    size = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b\b", name)
    if size and float(size.group(1)) <= COMPACT_AT_OR_BELOW_B:
        return "compact"
    for family, profile in FAMILIES.items():
        if family in name:
            return profile
    return "contract"


# --------------------------------------------------------------------------
# What each chip's score looks like
# --------------------------------------------------------------------------
#: The columns a briefing offers, what a cell in each holds, and the
#: header a score for the chip starts from. `fm5` is absent on purpose:
#: the DAC takes FM channel 6, and a briefing that offers both invites the
#: collision. `nes4` and `dac` take the same sample kit.
CARDS = {
    "YM2612": {
        "platform": "Sega Genesis / Mega Drive",
        "cols": "fm0 fm1 fm2 fm3 psg0 psg1 noise dac",
        "cells": [
            ("fm0-fm4", "`C-4` or `C-4:100` (velocity 1-127). Needs an "
                        "`inst` line"),
            ("psg0-psg2", "`C-5` (full volume) or `C-5:4` — after the "
                          "colon is ATTENUATION 0-15: 0 loudest, 15 "
                          "silent. Range A-2 to G#6"),
            ("noise", "`w0` `w1` `w2` white noise (w0 highest), `p0`-`p2` "
                      "periodic (buzzier)"),
            ("dac", "a drum sample: {samples}. `snare:0.6` plays it "
                    "quieter"),
        ],
        "instruments": "fm",
        "header": ["inst fm0 {bass}", "inst fm1 {lead}",
                   "inst fm2 {harmony}", "inst fm3 {pad}", "vol dac 55"],
    },
    "RP2A03": {
        "platform": "Nintendo NES / Famicom",
        "cols": "nes0 nes1 nes2 nes3 nes4",
        "cells": [
            ("nes0 nes1", "pulse waves: `E-5` or `E-5:90` (velocity "
                          "1-127). Lowest note A-1"),
            ("nes2", "triangle — the bass. `A-2` only: NO velocity, it "
                     "has no volume. Lowest note A-0"),
            ("nes3", "noise: a NUMBER `0`-`15` (0 = highest hiss), not a "
                     "note. `12m` metallic, `6:80` quieter"),
            ("nes4", "a drum sample: {samples}"),
        ],
        "instruments": None,
        "header": ["nes duty nes0 2", "nes duty nes1 1"],
    },
    "YM3812": {
        "platform": "AdLib / Sound Blaster PC",
        "cols": "opl0 opl1 opl2 opl3 opl4 opl5",
        "cells": [
            ("opl0-opl8", "`C-4` or `C-4:100` (velocity 1-127). One note "
                          "per column"),
        ],
        "instruments": "opl",
        "header": ["inst opl0 opl_bass", "inst opl1 opl_saw_lead",
                   "inst opl2 opl_pad", "inst opl3 opl_organ",
                   "inst opl4 opl_kick", "inst opl5 opl_snare"],
    },
}

#: Eight rows for each chip, in the header's own columns, so the example
#: and the header read as one score with one `cols` line. Tested to render
#: with no warnings at all: the one example a small model copies the
#: shape of has to be one that works.
EXAMPLES = {
    "YM2612": [
        "A-2:110 A-4 C-4 E-3 ...   ...   ... kick",
        "...     ... ... ... E-5:4 ...   w1  ...",
        "A-2     ... ... ... A-5:4 C-5:8 === snare",
        "...     C-5 ... ... E-5:4 ...   w1  ...",
        "G-2     ... B-3 D-3 ...   ...   === kick",
        "...     B-4 ... ... D-5:4 ...   w1  kick",
        "...     ... ... ... G-5:4 B-4:8 === snare",
        "===     G-4 === === D-5:4 ===   w1  ...",
    ],
    "RP2A03": [
        "E-5:100 C-5:70 A-2 ... kick",
        "...     ...    ... 9   ...",
        "G-5     E-5    ... === snare",
        "...     ...    ... 9   ...",
        "A-5     ...    E-2 === kick",
        "...     ...    ... 9   ...",
        "G-5     D-5    ... === snare",
        "===     ===    === 9   ...",
    ],
    "YM3812": [
        "A-1 E-4 C-4 A-3 C-2 ...",
        "... ... ... ... ... ...",
        "... G-4 ... ... ... C-4",
        "... ... ... ... ... ...",
        "G-1 A-4 B-3 G-3 C-2 ...",
        "... ... ... ... ... ...",
        "... G-4 ... ... ... C-4",
        "=== E-4 === === ... ...",
    ],
}

#: The rules a score is held to, by the same names the checker reports.
#: `text` is the rule; `why` is the reason, which the reasoned profile
#: prints and the others leave out. Each chip lists the rules that apply
#: to it, in the order a model should meet them.
RULES = {
    "cols_first": {
        "text": "Write the `cols` line before the first row. Every row "
                "has exactly one cell per column, separated by spaces; "
                "`...` is an empty cell",
        "why": "the parser counts cells against `cols`",
    },
    "note_format": {
        "text": "A note is letter, `-` or `#`, octave: `C-4`, `F#3`. "
                "Write sharps, not flats",
        "why": "one spelling, so nothing is misread",
    },
    "closed_lists": {
        "text": "Use only the instrument and sample names listed here; "
                "there are no others",
        "why": "an unknown name stops the render",
    },
    "chip_columns": {
        "text": "Use only this chip's columns",
        "why": "another chip's column plays on another chip",
    },
    "row_count": {
        "text": "Write every row of every bar. No placeholders, no "
                "\"repeat\" notes, no comments standing in for music",
        "why": "the renderer plays exactly the rows it is given",
    },
    "fm_needs_inst": {
        "text": "Every FM column (fm0-fm4) needs `inst fmN <name>` "
                "before its first note",
        "why": "an FM channel with no instrument is silent",
    },
    "fm5_vs_dac": {
        "text": "Do not use fm5: the dac column takes FM channel 6",
        "why": "the hardware cannot play both at once",
    },
    "psg_15_is_silent": {
        "text": "On psg0-psg2 the number after the colon is "
                "ATTENUATION: `:0` loudest, `:15` SILENT. Leave it off "
                "for full volume",
        "why": "the SN76489 has an attenuator, not a volume control",
    },
    "psg_floor": {
        "text": "Keep psg0-psg2 between A-2 and G#6. Put the bass on FM",
        "why": "below A-2 the divider clamps and the note plays A-2, up "
               "to +890 cents sharp",
    },
    "dac_level": {
        "text": "Keep `vol dac 55` in the header",
        "why": "at full level the drums take all the headroom and the "
               "music comes out quiet",
    },
    "nes_pulse_floor": {
        "text": "nes0 and nes1 go no lower than A-1",
        "why": "below it the timer clamps and the note sounds sharp",
    },
    "nes_triangle_floor": {
        "text": "nes2 goes no lower than A-0",
        "why": "below it the timer clamps",
    },
    "nes_triangle_velocity": {
        "text": "Never write `:N` on nes2",
        "why": "the triangle has no volume register",
    },
    "nes_noise_cells": {
        "text": "nes3 takes numbers 0-15, never notes",
        "why": "the noise channel has sixteen periods, not pitches",
    },
    "opl_drums": {
        "text": "There is no drum column: play opl_kick and opl_snare as "
                "notes on their own columns (kick around C-2, snare "
                "around C-4)",
        "why": "OPL2's rhythm mode is not implemented here",
    },
}

#: Rules a grammar-constrained decoder makes impossible to break (see
#: grammar.py). Under "grammar" delivery a briefing leaves them out: a
#: token spent teaching what the decoder already forbids is a token the
#: smallest models do not have.
GRAMMAR_ENFORCED = frozenset({
    "cols_first", "note_format", "closed_lists", "chip_columns",
    "fm5_vs_dac", "fm_needs_inst", "psg_15_is_silent", "psg_floor",
    "nes_pulse_floor", "nes_triangle_floor", "nes_triangle_velocity",
    "nes_noise_cells", "row_count", "dac_level",
})

CHIP_RULES = {
    "YM2612": ["cols_first", "note_format", "closed_lists", "chip_columns",
               "fm_needs_inst", "fm5_vs_dac", "psg_15_is_silent",
               "psg_floor", "dac_level", "row_count"],
    "RP2A03": ["cols_first", "note_format", "closed_lists", "chip_columns",
               "nes_pulse_floor", "nes_triangle_floor",
               "nes_triangle_velocity", "nes_noise_cells", "row_count"],
    "YM3812": ["cols_first", "note_format", "closed_lists", "chip_columns",
               "opl_drums", "row_count"],
}

#: Which chips brief() covers: the three this round is for. The SN76489
#: alone is a subset of the YM2612 card, and the existing starter() and
#: compose() still describe it.
BRIEF_CHIPS = tuple(CARDS)

#: Where the score goes and what to do with it, per delivery path.
DELIVERY = {
    "chat": "Reply with ONE fenced code block marked `trk` that contains "
            "the score and nothing else. No text before or after it.",
    "agent": "Write the score to `work/song.trk`, then run "
             "`python3 python/chipgen.py work/song.trk --check` and fix "
             "what it reports IN THE SCORE. You are the composer here, not "
             "the developer: never edit anything in `python/`, `core/`, "
             "`bridge/` or `tests/`. If the engine looks wrong, say so and "
             "stop — a "
             "changed engine is not chipgen, and the render says so.",
    "grammar": "Write the score and nothing else.",
}

#: The last line of an agent's brief: what to hand back. An MP3, because
#: the file is the most expensive thing in the exchange — a WAV is ten
#: megabytes a minute, the MP3 a tenth of that and playable anywhere.
HAND_BACK = ("When it says PLAYABLE: `python3 python/chipgen.py "
             "work/song.trk -o work/song.mp3`. Hand back the MP3, not a WAV "
             "— a tenth of the size.")


def estimate_tokens(text: str) -> int:
    """About how many tokens a briefing costs. Four characters a token.

    An estimate, and labelled as one: the exact count depends on the
    tokenizer, which differs by family. It is a budget that stays
    comparable between briefings, which is what it is used for.
    """
    return (len(text) + 3) // 4


def instrument_names(kind: str) -> list:
    if kind == "fm":
        import instruments
        return sorted(instruments.describe())
    if kind == "opl":
        import opl_instruments
        return sorted(opl_instruments.names())
    return []


def _instrument_line(kind: str, with_character: bool) -> str:
    names = instrument_names(kind)
    if kind == "fm" and with_character:
        import instruments
        described = instruments.describe()
        parts = []
        for name in names:
            character = described[name].get("character", "")
            short = character.split(",")[0].strip()
            parts.append(f"{name} ({short})" if short else name)
        return "; ".join(parts)
    return " ".join(names)


def _palette() -> dict:
    """The measured default FM cast — bass, lead, harmony, pad.

    From selection.palette(), which ranks the bank by what each patch
    measures like, not by its name. A template that assigned `bass` to
    the bass part because of the word would be the thing this project
    keeps finding wrong.
    """
    try:
        import selection
        chosen = selection.palette()
        return {part: row["name"] for part, row in chosen.items()}
    except Exception:
        return {"bass": "bass", "lead": "saw_lead", "harmony": "e_piano",
                "pad": "strings"}


def _number(value, default):
    """A request number — or a `{placeholder}` passed through untouched,
    which is how brief_template() leaves the blanks an interface fills."""
    if isinstance(value, str) and value.startswith("{"):
        return value
    return int(float(value or default))


def template(chip: str, request: dict = None) -> list:
    """The header a score for this chip starts from, filled in."""
    request = request or {}
    card = CARDS[chip]
    lines = [f"bpm {_number(request.get('bpm'), 140)}", "lpb 4"]
    if chip == "YM2612":
        palette = _palette()
        lines += [line.format(**palette) for line in card["header"]]
    else:
        lines += list(card["header"])
    lines.append(f"cols {card['cols']}")
    return lines


def rows_for(request: dict = None):
    """Rows a request asks for: bars x 4 beats x 4 rows a beat."""
    bars = _number((request or {}).get("bars"), 8)
    if isinstance(bars, str):
        return "{rows}"
    return max(1, bars) * 16


def brief(chip: str = "YM2612", model: str = None, request: dict = None,
          delivery: str = "chat") -> str:
    """A complete, self-contained briefing for one model and one chip.

    `model` is a family, a model name or a profile name (profile_for()
    decides). `delivery` is "chat" (a reply with a fenced score),
    "agent" (a sandbox with the archive: write a file, run --check, do
    not touch the engine) or "grammar" (constrained decoding: the score
    and nothing else).
    """
    import samples as samples_mod

    if chip not in CARDS:
        raise ValueError(f"no briefing for {chip!r}; have "
                         f"{', '.join(BRIEF_CHIPS)}")
    if delivery not in DELIVERY:
        raise ValueError(f"delivery is one of {', '.join(DELIVERY)}")
    profile_name = profile_for(model)
    profile = PROFILES[profile_name]
    request = request or {}
    card = CARDS[chip]
    samples = " ".join(samples_mod.names())
    contract = DELIVERY[delivery]

    out = [f"You write chiptune scores for chipgen, which plays them on "
           f"emulated {chip} hardware ({card['platform']}). The score is "
           f"a text grid: one line per row of time, one column per "
           f"voice."]
    if profile["repeat_contract"]:
        out += ["", contract]

    # The request first for the strict profiles: they anchor on what is
    # asked before the rules; the reasoned profile reads it at the end.
    ask = _ask(chip, request)
    if profile_name != "reasoned":
        out += ["", *ask]

    out += ["", "FORMAT",
            "Header lines, then rows:",
            "  bpm <n>         tempo",
            "  lpb 4           rows per beat (4 = sixteenth notes)"]
    if card["instruments"]:
        first = "fm0" if card["instruments"] == "fm" else "opl0"
        out.append(f"  inst {first} <name>  the instrument for a column")
    if chip == "RP2A03":
        out.append("  nes duty nes0 2  pulse timbre 0-3 (0 thinnest, "
                   "2 square)")
    if chip == "YM2612":
        out.append("  vol dac 55      drum level")
    out += ["  cols ...        the columns, in order",
            "Cells: `...` = nothing new (hold or rest), `===` = note "
            "off."]
    for columns, what in card["cells"]:
        out.append(f"  {columns}: {what.format(samples=samples)}")

    if card["instruments"]:
        out += ["", "INSTRUMENTS (the only ones):",
                "  " + _instrument_line(card["instruments"],
                                        with_character=profile_name
                                        != "compact")]

    rules = [r for r in CHIP_RULES[chip]
             if not (delivery == "grammar" and r in GRAMMAR_ENFORCED)]
    if rules:
        out += ["", "RULES"]
        for index, rule_id in enumerate(rules, 1):
            rule = RULES[rule_id]
            line = f"{index}. {rule['text']}."
            if profile["reasons"]:
                line = f"{index}. {rule['text']} — {rule['why']}."
            out.append(line)

    rows = EXAMPLES[chip][:profile["example_rows"]]
    out += ["", "A SCORE STARTS LIKE THIS — keep the header (instruments "
                "may change), replace these rows with your own music:",
            *template(chip, request), *rows]

    if profile_name == "reasoned":
        out += ["", *ask]
    out += ["", contract if not profile["repeat_contract"]
            else f"Again: {contract}"]
    if delivery == "agent":
        out.append(HAND_BACK)
    return "\n".join(out)


def _ask(chip: str, request: dict) -> list:
    description = (request.get("prompt") or "").strip()
    lines = ["TASK"]
    lines.append(f"Write an original piece: "
                 f"{description}" if description
                 else "Write an original piece.")
    details = []
    for key, label in (("key", "key"), ("style", "style")):
        if request.get(key):
            details.append(f"{label} {request[key]}")
    details.append(f"{_number(request.get('bpm'), 140)} BPM")
    bars = _number(request.get("bars"), 8)
    details.append(f"{bars} bars = exactly {rows_for(request)} rows")
    lines.append("; ".join(details) + ".")
    return lines


def agents_md() -> str:
    """AGENTS.md for the bridge archive — what an agent reads on arrival.

    Several agent tools read a file of this name before they do anything
    else, which makes it the one document a model that skims is certain
    to see. It goes into the archive only, never the repository root: in
    a development checkout an agent IS meant to change the engine, and a
    file telling it not to would be wrong there.
    """
    chips = ", ".join(f"{chip} ({CARDS[chip]['platform']})"
                      for chip in BRIEF_CHIPS)
    return "\n".join([
        "# You are the composer here",
        "",
        f"This archive is chipgen: an engine that plays scores on emulated "
        f"sound chips — {chips}. Your job is to write scores. The engine "
        f"is finished, tested and checksummed.",
        "",
        "## Rules of engagement",
        "",
        "1. Never edit `python/`, `core/`, `bridge/` or `tests/`. Every "
        "render reports an edited engine file, and a render from an edited "
        "engine is not chipgen.",
        "2. Your files go in `work/`: scores, and — if you need a sound "
        "the bank lacks — a patch file passed with `--bank work/bank.json`, "
        "or a scenario for `python3 python/forge.py run work/forge/x.json "
        "--out work/forge/x.bank.json` that describes the sound as dials "
        "(`bridge/SYNTHESIS.md`; the result is passed with `--forge-bank`, "
        "or `--forge-bank python/synthesis/banks/starter.bank.json` for 42 "
        "ready ones). Never write operator registers into `python/`.",
        "3. If the engine seems wrong, say so and stop. Do not work around "
        "it.",
        "",
        "## The loop",
        "",
        "1. `python3 bridge/bootstrap.py` — once; builds and self-tests.",
        "2. `python3 python/chipgen.py --brief --chip-target <CHIP> "
        "--family <your model family>` — everything you need for that "
        "chip, in a few hundred tokens. Read it.",
        "3. Write `work/song.trk`.",
        "4. `python3 python/chipgen.py work/song.trk --check --chip-target "
        "<CHIP>` — fix what it reports, in the score. Repeat until it says "
        "PLAYABLE.",
        "5. `python3 python/chipgen.py work/song.trk -o work/song.mp3 "
        "--vgm work/song.vgz` — hand back the MP3: about a tenth of a WAV, "
        "and it plays anywhere. Cheaper still is the `.trk` itself, a few "
        "kilobytes.",
        "",
        f"`<CHIP>` is {', '.join(BRIEF_CHIPS)}. The brief is enough to "
        f"write a score; `START_HERE.md` and `bridge/CORE.md` go deeper, "
        f"for instrument design rather than a first track.",
        "",
        "## A longer piece, heard and corrected as it grows (YM2612)",
        "",
        "`python3 python/agent.py` keeps the piece in a project under "
        "`work/agentic/`, not in your context: `create_musical_state`, "
        "`continue_composition` (section by section, resumable), "
        "`hear_range` (a measurement of the render, not listening), "
        "`improve` (a measured fix, kept or undone), `export`. With "
        "references (`add_reference`: a recording, a score or a module, "
        "and what it is a reference for), `listen_compare` cuts the same "
        "bars from each, matched in loudness, and "
        "`improve_toward_reference` tries changes that carry their "
        "principles over (never their notes). Every reply is short JSON "
        "and says whether anything listened; `bridge/AGENTIC.md` lists "
        "the operations.",
        "",
    ])


#: The blanks brief_template() leaves for an interface to fill.
PLACEHOLDERS = {"prompt": "{prompt}", "bpm": "{bpm}", "bars": "{bars}"}


def brief_template(chip: str, profile: str, delivery: str = "chat") -> str:
    """A brief with `{prompt}`, `{bpm}`, `{bars}` and `{rows}` left blank.

    For the static interface, which has no Python: it fills the blanks by
    string replacement and sends exactly what brief() would have built —
    `rows` is bars x 16. Key and style go into `{prompt}`.
    """
    return brief(chip, profile, dict(PLACEHOLDERS), delivery=delivery)


def briefs_table() -> dict:
    """Every chip x profile, with its size — for the contract and for the
    budget tests."""
    out = {}
    for chip in BRIEF_CHIPS:
        for profile in PROFILES:
            for delivery in DELIVERY:
                text = brief(chip, profile, delivery=delivery)
                out[f"{chip}/{profile}/{delivery}"] = {
                    "tokens": estimate_tokens(text),
                    "chars": len(text),
                }
    return out


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
