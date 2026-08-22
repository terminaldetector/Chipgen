"""
cloud_generator.py — same contract as demo_generator.py (returns
List[Event]), but the events come from a CLOUD model instead of a local
rule-based stub or a locally-hosted network. Proves the "any neural
network" claim isn't limited to in-process/local models: this is a
synchronous network call, and the Sequencer doesn't know or care.

Two things changed since the first version, both because the project grew
a second front door:

  * THE PROMPT IS GENERATED, NOT WRITTEN. It is built from
    events.describe_vocabulary(), instruments.describe() and samples.names()
    at call time. Adding an event type or a patch used to mean remembering
    to edit a string literal here, and the failure mode was silent: the
    model simply never used the new thing. Now it cannot drift.

  * IT CAN ASK FOR TRACKER TEXT INSTEAD OF JSON (`fmt="tracker"`, the
    default). A four-bar pattern is a few hundred bytes of tracker grid
    against several thousand of JSON, which is cheaper, and the model can
    see the rhythm it is writing instead of counting braces. JSON is still
    there for models that are better at structured output than at grids.

Uses the Anthropic API as the concrete example, but the pattern is
identical for any provider: build the prompt, demand one format, parse,
validate. Swap `_call_model()` for OpenAI/Gemini/whatever — nothing else
in this file, or in sequencer.py, changes.

Requires: pip install anthropic
          export ANTHROPIC_API_KEY=...
"""

import json
import os
import re
from typing import List

import events as events_mod
import instruments as instruments_mod
import samples as samples_mod
import tracker as tracker_mod
from events import Event

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_EVENTS = 800


# --------------------------------------------------------------------------
# Prompt construction — derived from the code, never hand-copied
# --------------------------------------------------------------------------
def instrument_catalogue() -> str:
    return "\n".join(f"  {name:<15} {info['character']}"
                     for name, info in instruments_mod.describe().items())


def event_catalogue() -> str:
    lines = []
    for name, spec in events_mod.describe_vocabulary().items():
        fields = []
        for field, meta in spec["fields"].items():
            text = f'"{field}": <{meta["type"]}'
            if "range" in meta:
                text += f' {meta["range"][0]}..{meta["range"][1]}'
            elif "values" in meta:
                text += " " + "|".join(meta["values"])
            text += ">"
            if "default" in meta:
                text += f" (optional, default {meta['default']!r})"
            fields.append(text)
        body = ", ".join(fields)
        lines.append(f'  {{"type": "{name}"' + (", " + body if body else "") + "}")
        doc = spec["doc"].split("\n")[0].strip()
        if doc:
            lines.append(f"      {doc}")
    return "\n".join(lines)


def build_system_prompt(fmt: str, ticks_per_second: float) -> str:
    shared = f"""\
You are composing a chiptune pattern for a Sega Genesis engine that runs \
real YM2612 FM and SN76489 PSG emulation — not a synthesiser imitating \
that sound. What you write is executed by the chips exactly as written, so \
compose deliberately: pick a key, give the lead a contour, let phrases \
breathe.

The hardware, and what it will and will not let you do:
  - 6 FM channels (0-5), ONE NOTE EACH. A triad needs three channels.
  - 3 PSG square channels (0-2) plus one shared noise voice.
  - PSG volume is an ATTENUATOR: 0 is loudest, 15 is silent.
  - The PSG's tone register is 10 bits, so above about C6 neighbouring
    semitones collide. Leads belong on FM; give the PSG arpeggios.
  - Drums are PCM samples through the DAC, which takes over FM channel 5
    while one plays: {", ".join(samples_mod.names())}.

Instruments (reference by name):
{instrument_catalogue()}
"""

    if fmt == "tracker":
        return shared + f"""
Output ONLY a tracker score. No prose, no markdown fences, nothing around it.

Directives, one per line, before or between rows:
  bpm <n>              tempo
  lpb <n>              rows per beat (4 = sixteenth notes)
  inst fm<0-5> <name>  assign an instrument to a channel
  vol fm<0-5> <0-127>  channel volume    | vol psg<0-2> <0-15>
  pan fm<0-5> L|R|C    stereo placement
  lfo on <0-7>         global vibrato/tremolo LFO
  pitch fm<0-5> <cents>  detune
  cols <columns...>    which columns the rows below carry
  loop                 where a looping player returns to
  title / author       metadata

Columns: fm0..fm5, psg0..psg2, noise, dac.
Cells:
  fm      A-2  A#3  A-2:100 (velocity)  ===  (note off)  ...  (hold)
  psg     A-4  A-4:8 (volume)           ===              ...
  noise   w0-w3 (white)  p0-p3 (periodic)  ===           ...
  dac     {" ".join(samples_mod.names())}                 ...

Every row must have exactly as many cells as `cols` names. Example:

{tracker_mod.__doc__.split("So the same pattern in tracker notation:")[1].split("Same music")[0].strip()}
"""

    return shared + f"""
Output ONLY a JSON array. No prose, no markdown fences, nothing before or
after the array. There are {ticks_per_second:g} ticks per second; Wait
advances the clock and is the only thing that does.

Event types:
{event_catalogue()}

Rules:
  - End the array with {{"type": "End"}}.
  - FMInstrumentSelect a channel before its first FMNoteOn.
  - Release a note (FMNoteOff / PSGToneOff) before retriggering it, unless
    you mean the overlap.
  - Stay under {MAX_EVENTS} events.
"""


def build_user_prompt(style: str, bars: int, bpm: int) -> str:
    return (f"Style brief: {style}\n"
            f"Length: {bars} bars at {bpm} BPM.\n"
            f"Output the score now.")


# --------------------------------------------------------------------------
# Provider call — the one function to swap
# --------------------------------------------------------------------------
def _call_model(system: str, user: str, model: str = DEFAULT_MODEL) -> str:
    """The only function you'd swap for a different provider. Must return
    the raw text response."""
    import anthropic
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content
                   if block.type == "text")


# --------------------------------------------------------------------------
# Response handling
# --------------------------------------------------------------------------
def strip_fences(text: str) -> str:
    """Models add markdown fences however firmly you ask them not to."""
    text = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", text, re.DOTALL)
    return fence.group(1).strip() if fence else text


def _extract_json_array(text: str) -> list:
    text = strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"could not find a JSON array in model output:\n{text[:500]}")


def parse_and_validate(raw_json: list):
    """Tolerant parse: returns (events, warnings).

    A take that is 95% right should be heard, not thrown away over one
    out-of-range channel — see events.parse for exactly what gets repaired.
    """
    return events_mod.parse(raw_json)


def generate_pattern_cloud(style: str = "dark digital fusion hardcore, Genesis FM",
                           bars: int = 4, bpm: int = 172,
                           ticks_per_second: float = 192.0,
                           model: str = DEFAULT_MODEL,
                           fmt: str = "tracker",
                           return_warnings: bool = False) -> List[Event]:
    if fmt not in ("tracker", "json"):
        raise ValueError(f"fmt must be 'tracker' or 'json', got {fmt!r}")

    system = build_system_prompt(fmt, ticks_per_second)
    raw_text = _call_model(system, build_user_prompt(style, bars, bpm), model=model)

    if fmt == "tracker":
        events, _ = tracker_mod.loads(strip_fences(raw_text))
        warnings = []
    else:
        events, warnings = parse_and_validate(_extract_json_array(raw_text))

    return (events, warnings) if return_warnings else events


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    output = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "output")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # No key is not a failure: show that parsing and validation stand on
        # their own, which is the part that breaks in practice.
        print("ANTHROPIC_API_KEY is not set — running the offline path.\n")
        mock = json.dumps([
            {"type": "FMInstrumentSelect", "channel": 0, "instrument": "bass"},
            {"type": "FMNoteOn", "ch": 0, "note": "a", "octave": 2},
            {"type": "Wait", "ticks": 24},
            {"type": "FMNoteOff", "channel": 0},
        ])
        print("a model might return:", mock)
        events, warnings = parse_and_validate(_extract_json_array(mock))
        print(f"\nparsed {len(events)} events with {len(warnings)} repair(s):")
        for warning in warnings:
            print(f"  - {warning}")
        print(f"\nsystem prompt is {len(build_system_prompt('tracker', 192.0))} "
              f"chars in tracker mode, "
              f"{len(build_system_prompt('json', 192.0))} in JSON mode")
        raise SystemExit(0)

    import wavio
    import vgm
    from sequencer import Sequencer

    events, warnings = generate_pattern_cloud(return_warnings=True)
    print(f"got {len(events)} events from the cloud model")
    for warning in warnings:
        print(f"  repair: {warning}")

    seq = Sequencer()
    audio = seq.render_to_file(events,
                               os.path.join(output, "cloud_demo.wav"),
                               vgm_path=os.path.join(output, "cloud_demo.vgm"),
                               gd3=vgm.GD3(title="chipgen cloud demo"))
    print(wavio.describe(audio, seq.target_rate))
