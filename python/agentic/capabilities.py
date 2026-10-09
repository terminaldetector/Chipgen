"""capabilities.py — what each backend can do for the agent, said plainly.

Every action is listed per target with:

    status      Supported / Unsupported
    mode        Native (the chip or format does it), Emulated (the engine
                reproduces it by other means), Approximate (close, with a
                stated loss)
    accuracy    what was measured, or the known loss
    cost        what it takes (renders, seconds)
    check       how the result can be verified

The agentic loop (MusicalState, range render, ear, patch, loop, compose)
runs on the Mega Drive. The engine renders NES and OPL2 as well, and the
Schism project writes Impulse Tracker modules, but the loop is not wired
to them: their rows say so instead of degrading silently, and asking for
them is refused with the reason (state.new_state, `check_action`).

`export` writes the project in the formats that exist and refuses the
rest by name; nothing is dropped without a word.
"""

import os
from typing import Optional

from .state import AgenticError, content

_MD = {
    "render": ("Supported", "Native",
               "Nuked-OPN2 (cycle-level YM2612 core) and the SN76489 "
               "emulator; the levels are the chip's",
               "about 2.5-3x faster than real time per render (measured "
               "here, pure-Python mixer, no numpy)",
               "the render is the reference"),
    "render_range": ("Supported", "Emulated",
                     "state chased in (instrument, operator, volume, pan "
                     "writes replayed; held notes re-keyed) with a 2 s "
                     "pre-roll. Against a full render: FM stems' level "
                     "envelope within 0.05 dB; PSG square and noise "
                     "envelopes 0.0 and 0.5 dB (p95 2.6), their waveform "
                     "phase differs (the oscillator and LFSR restart); a "
                     "DAC hit before the window is not replayed",
                     "one render of the window plus pre-roll and tail",
                     "render.chase_error against method='full'"),
    "stems": ("Supported", "Native",
              "one voice kept, every other channel's events dropped, the "
              "chip rendered again; the mix is not the sum of the stems "
              "(register writes take chip time: +2.9 dB measured on two "
              "FM voices), so a background is rendered, never subtracted",
              "one render per stem", "levels.isolate"),
    "register_trace": ("Supported", "Native",
                       "every write the emulated chips receive, decoded "
                       "(key, operator fields, frequency, pan); DAC sample "
                       "bytes counted, not listed",
                       "one silent run of the sequencer", "trace.explain"),
    "patch.level": ("Supported", "Native",
                    "FM: velocity, carrier TL in 0.75 dB steps; PSG and "
                    "noise: attenuation, 2 dB steps; DAC: PCM volume "
                    "(Approximate: the bytes are scaled). The realised dB "
                    "is reported next to the asked", "none until rendered",
                    "the delta's realised_db"),
    "patch.transpose": ("Supported", "Native",
                        "semitones; refused under the PSG's A-2 floor (the "
                        "divider clamps, the note sounds sharp) and outside "
                        "octaves 0-7", "none until rendered",
                        "pitch classes kept for octave moves"),
    "patch.param": ("Supported", "Native",
                    "operator fields of modulators, whole piece or a "
                    "range; carrier TL refused (a key-on below velocity "
                    "127 rewrites it from the bank patch)",
                    "none until rendered", "register_trace"),
    "patch.vol": ("Supported", "Native", "the channel's vol over a range, "
                  "restored after", "none until rendered", "register_trace"),
    "ear.tool": ("Supported", "Native",
                 "measurements of the render: in-band margin per note, "
                 "who fills a band, unheard notes, long holds, clipping. "
                 "No click detector (it fired on PSG square edges). Not "
                 "listening", "1 + 2 x voices renders, cached",
                 "the numbers are reproducible"),
    "ear.native": ("Supported", "Native",
                   "only with an adapter that passes the audio probes "
                   "(tone count, higher/lower); its judgement is reported "
                   "as an auditory model's, with the model's name",
                   "one audio request per range; audio seconds counted "
                   "apart from text tokens", "ear.verify"),
    "ear.hybrid": ("Supported", "Native",
                   "both; each auditory observation cross-checked against "
                   "the measurement (confirmed / not confirmed / not "
                   "measurable)", "both costs", "cross_check"),
    "compose.builtin": ("Supported", "Native",
                        "plain rules: motif development, harmony on strong "
                        "and long notes, voice-led pad, walking bass, drum "
                        "fills; deterministic per seed", "milliseconds",
                        "compose.harmony_check, motif.analyse"),
    "compose.agent_rows": ("Supported", "Native",
                           "rows written by the agent, parsed by the "
                           "tracker; the same checks run", "none",
                           "timeline.build"),
    "dac_and_fm5": ("Unsupported", None,
                    "the DAC plays through FM channel 6: a project's voices "
                    "use fm5 or dac, not both (state.check_content refuses "
                    "it; the engine's sanity check measures the masking "
                    "when a plain score does both)", None, "check_content"),
    "export.trk": ("Supported", "Native", "the state's own notation, "
                   "lossless", "none", "tracker.loads"),
    "export.vgm": ("Supported", "Native", "the register log the chips "
                   "received", "one render", "vgm_player"),
    "export.wav": ("Supported", "Native", "16-bit PCM of the render",
                   "one render", "wavio.read"),
    "export.mp3": ("Supported", "Approximate", "lossy audio (LAME or the "
                   "built-in encoder)", "one render", "mp3 decoders"),
    "export.it": ("Supported", "Approximate",
                  "FM patches become samples at one reference pitch "
                  "(timbre drifts across octaves); notes, order, "
                  "velocities, panning and drums survive exactly",
                  "one render per patch", "libopenmpt"),
    "export.mid": ("Unsupported", None, "no MIDI writer in the engine",
                   None, None),
    "export.nsf": ("Unsupported", None, "no NES driver export; and the "
                   "loop is not wired to the NES", None, None),
}

_OTHER = {
    "nes": {"render": ("Supported", "Emulated",
                       "the 2A03 pulse, triangle, noise and DMC in Python "
                       "(nes_apu)", "one render", "nes_apu"),
            "agentic_loop": ("Unsupported", None,
                             "MusicalState and the range render know the "
                             "Mega Drive's columns only", None, None)},
    "opl2": {"render": ("Supported", "Emulated",
                        "YM3812 emulation (opl2.py)", "one render",
                        "opl2"),
             "agentic_loop": ("Unsupported", None,
                              "not wired: no OPL columns in the state",
                              None, None)},
    "schism": {"render": ("Supported", "Native",
                          "Impulse Tracker modules rendered by libopenmpt "
                          "in the Schism project (a separate branch), "
                          "verified against Schism's own disk writer",
                          "one render", "libopenmpt"),
               "agentic_loop": ("Unsupported", None,
                                "the Schism forge has its own loop for "
                                "instruments; this one (state, ear, patch, "
                                "compose) is not ported to IT samples, "
                                "envelopes, NNA or effect memory", None,
                                None)},
}

TARGETS = ("megadrive", "nes", "opl2", "schism")


def _row(spec) -> dict:
    status, mode, accuracy, cost, check = spec
    return {"status": status, "mode": mode, "accuracy": accuracy,
            "cost": cost, "check": check}


def inspect(target: str = "megadrive", adapter=None,
            verification: Optional[dict] = None) -> dict:
    """The capability manifest for a target, and for the agent's audio
    adapter if one is given (whether it was verified to hear)."""
    if target not in TARGETS:
        raise AgenticError("unknown_target", f"target is one of "
                           f"{', '.join(TARGETS)}, not {target!r}",
                           target=target)
    rows = _MD if target == "megadrive" else _OTHER[target]
    out = {"target": target,
           "agentic_loop": "Supported" if target == "megadrive"
           else "Unsupported",
           "actions": {k: _row(v) for k, v in rows.items()}}
    ears = {"tool": "available"}
    if adapter is None:
        ears["native"] = "unavailable: no audio adapter configured"
    elif verification and verification.get("verified"):
        ears["native"] = f"available: {adapter.name} answered the probes"
    else:
        why = (verification or {}).get("why", "not verified")
        ears["native"] = (f"unavailable: {adapter.name} did not pass the "
                          f"probes ({why})")
    ears["hybrid"] = "available" if ears["native"].startswith(
        "available") else "unavailable: needs a verified native ear"
    out["ears"] = ears
    return out


def check_action(target: str, action: str) -> dict:
    """One action's row; Unsupported raises with the reason."""
    rows = _MD if target == "megadrive" else _OTHER.get(target, {})
    if action not in rows:
        if target != "megadrive" and "agentic_loop" in rows:
            spec = rows["agentic_loop"]
            raise AgenticError("unsupported", f"{target}: {action} is not "
                               f"available — {spec[2]}", target=target,
                               action=action)
        raise AgenticError("unknown_action", f"{action!r} is not an action "
                           f"of {target}", target=target, action=action)
    row = _row(rows[action])
    if row["status"] == "Unsupported":
        raise AgenticError("unsupported", f"{target}: {action} — "
                           f"{row['accuracy']}", target=target,
                           action=action)
    return row


def export(project, fmt: str, path: Optional[str] = None) -> dict:
    """The project's head in `fmt` (trk, vgm, vgz, wav, mp3, it). Other
    formats are refused by name with the reason."""
    from . import timeline as T
    fmt = fmt.lower().lstrip(".")
    kind = "vgm" if fmt == "vgz" else fmt      # a gzipped VGM, as players expect
    row = check_action("megadrive", f"export.{kind}") if \
        f"export.{kind}" in _MD else None
    if row is None:
        raise AgenticError("unsupported_format",
                           f"{fmt!r} is not a format this engine writes "
                           f"(trk, vgm, vgz, wav, mp3, it)", format=fmt)
    c = content(project.state)
    tl = T.build(c, title=project.state["identity"]["id"])
    path = path or project.path("out", f"head.{fmt}")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if fmt == "trk":
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(tl.text)
    else:
        import chipgen
        chipgen.compose(tl.text, **{kind: path})
    return {"format": fmt, "path": path, "bytes": os.path.getsize(path),
            "mode": row["mode"], "accuracy": row["accuracy"],
            "rev": project.head}
