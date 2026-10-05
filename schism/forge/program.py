"""program.py — what an IT instrument does over the life of a note, said the
way the file holds it.

On Chipgen a program is a list of register writes the driver makes while a
note sounds. Impulse Tracker has no driver to program: the instrument header
*is* the program. Three envelopes (volume, pan, and one slot that is either
pitch or filter) run on the module's tick from every note-on, with a
sustain loop that holds while the key is down and a loop that does not care;
the NNA says what a new note does to the one still sounding; the fade-out
takes over at the key-off; the sample adds its own loop and auto-vibrato.
The notation already writes all of that (`venv=`, `penv=`, `fenv=`, `ienv=`,
`nna=`, `fade=`), and the forge builds it from seconds (patch.py).

What this module adds is the account a small model needs before it touches
one of those numbers:

    capabilities()     every target, its units and range, its clock, what
                       restarts it, what a key-off does to it, what in a
                       pattern also moves it, and the limits of the format
    explain_instrument the envelopes of an instrument in a module, node by
                       node in ticks and in milliseconds at the module's
                       tempo, and what a key-off and the next note do
    explain_patch      the same for a forge patch, written in seconds: where
                       each node landed on the tick grid, how far it moved,
                       which nodes merged into one (the later wins) — said,
                       not silently done

Nothing here is finer than a tick: a tick is 2.5/tempo seconds (20 ms at the
default 125), and an envelope written for one tempo runs faster or slower at
another. That is the format, and the explanation says it.
"""

from typing import Optional

from .. import model as M
from . import patch as patch_mod

NNA = {0: "cut", 1: "continue", 2: "note off", 3: "note fade"}
DCT = {0: "off", 1: "note", 2: "sample", 3: "instrument"}
DCA = {0: "cut", 1: "note off", 2: "note fade"}

_TARGETS = [
    {"target": "venv (volume envelope)", "units": "0..64, multiplies the "
     "note's volume", "nodes": "1-25, tick:value",
     "interp": "linear between nodes, a step a tick",
     "restart": "every note-on, unless the envelope carries (`carry`): "
                "then a note that follows a note of the same instrument "
                "still held on the channel goes on from where that one "
                "is; after a key-off or a note cut, or with another "
                "instrument, it starts again (measured, both players)",
     "key_off": "leaves the sustain loop and runs on through the nodes "
                "after it; with no sustain loop a key-off changes nothing "
                "but starts the fade-out",
     "pattern": "the volume column and Dxy set the note's volume, which the "
                "envelope multiplies; Mxx/Nxy (channel volume) and Vxx/Wxy "
                "(global) multiply on top"},
    {"target": "penv (pan envelope)", "units": "-32 (left)..32 (right), added "
     "to the channel's pan", "nodes": "1-25", "interp": "linear",
     "restart": "as the volume envelope", "key_off": "as the volume "
     "envelope",
     "pattern": "Xxx / the volume column's pan set the base it moves around; "
                "Pxy slides it"},
    {"target": "ienv (pitch envelope)", "units": "-32..32 half-semitones "
     "(32 = 16 semitones)", "nodes": "1-25", "interp": "linear",
     "restart": "as the volume envelope", "key_off": "as the volume envelope",
     "pattern": "adds to Exx/Fxx/Gxx slides and Hxy vibrato; shares its slot "
                "with fenv: an instrument has one or the other"},
    {"target": "fenv (filter envelope)", "units": "0..64: the share of the "
     "instrument's cutoff in use (64 = as set)", "nodes": "1-25",
     "interp": "linear", "restart": "as the volume envelope",
     "key_off": "as the volume envelope",
     "pattern": "Zxx (with IT's default macros) sets the cutoff the envelope "
                "scales; shares its slot with ienv"},
    {"target": "nna", "units": "cut | cont | off | fade",
     "nodes": "-", "interp": "-",
     "restart": "-", "key_off": "-",
     "pattern": "S70-S72 act on the notes left in the background, S73-S76 "
                "override the NNA for one note; dct/dca decide what a "
                "repeated note does to its own background copy"},
    {"target": "fade", "units": "0..256: how fast the note fades after a "
     "key-off (or when a fade NNA sends it to the background); 0 = never",
     "nodes": "-", "interp": "per tick", "restart": "-",
     "key_off": "starts it", "pattern": "~~~ (note fade) starts it too"},
    {"target": "vib (sample auto-vibrato)", "units": "speed, depth, rate "
     "(the sweep: how fast it reaches full depth), wave", "nodes": "-",
     "interp": "per tick", "restart": "every note-on (the sweep starts "
     "again, so it is a delayed vibrato)", "key_off": "unchanged",
     "pattern": "adds to Hxy/Uxy vibrato"},
    {"target": "sample loop / sustain loop", "units": "frames",
     "nodes": "-", "interp": "-", "restart": "every note-on (Oxx starts "
     "elsewhere)", "key_off": "a sustain loop is left at the key-off; a "
     "loop keeps looping", "pattern": "Oxx sample offset, SAx high offset"},
]

_LIMITS = [
    "25 nodes an envelope; ticks rise; the first node at tick 0",
    "pitch and filter share one envelope slot",
    "a tick is 2.5/tempo s: an envelope written in ticks is slower at a "
    "slower tempo; one built from seconds is right only at the tempo it "
    "was built for (the forge builds at the module's)",
    "values are whole numbers: volume steps of 1/64, pan 1/32, pitch half "
    "a semitone (50 cents), filter 1/64 of the cutoff",
    "legato is not an instrument setting in IT: it is Gxx (tone "
    "portamento) on the next note in the pattern",
    "one effect column a cell, plus the volume column: two effects on one "
    "note need the volume column (it holds slides, vibrato depth, pan and "
    "portamento in a short form), a second row, or a second channel",
    "a sample made from an FM render is a Schism instrument with an FM "
    "origin, not a live YM2612 channel; its timbre no longer follows "
    "operator writes",
]


def capabilities() -> dict:
    return {"engine": "it (Schism Tracker / libopenmpt)",
            "clock": "the module's tick, 2.5/tempo s (20 ms at tempo 125); "
                     "rows are `speed` ticks",
            "targets": [dict(t) for t in _TARGETS],
            "limits": list(_LIMITS)}


def _tick_ms(tempo: int) -> float:
    return 2500.0 / max(1, tempo)


def _env(env: Optional[M.Envelope], tick_ms: float, label: str) -> Optional[dict]:
    if env is None or not env.enabled:
        return None
    out = {"slot": label,
           "nodes": [{"tick": t, "ms": round(t * tick_ms, 1), "value": v}
                     for t, v in env.nodes],
           "sustain": list(env.sustain) if env.sustain else None,
           "loop": list(env.loop) if env.loop else None,
           "carry": bool(getattr(env, "carry", False)),
           "length_ms": round(env.nodes[-1][0] * tick_ms, 1)
           if env.nodes else 0.0}
    if env.sustain:
        a, b = env.sustain
        out["held"] = (f"holds at node {a}" if a == b else
                       f"loops nodes {a}-{b} "
                       f"({(env.nodes[b][0] - env.nodes[a][0]) * tick_ms:.0f}"
                       f" ms) while the key is down")
    return out


def explain_instrument(module: M.Module, number: int,
                       tempo: Optional[int] = None) -> dict:
    """An instrument of a module, as the players will run it."""
    ins = module.instruments[number - 1]
    tempo = tempo or module.tempo
    tick_ms = _tick_ms(tempo)
    pitch_slot = None
    if ins.pitch_envelope is not None and ins.pitch_envelope.enabled:
        pitch_slot = "filter" if ins.pitch_envelope.filter else "pitch"
    out = {"instrument": number, "name": ins.name, "tempo": tempo,
           "tick_ms": round(tick_ms, 3),
           "volume": _env(ins.volume_envelope, tick_ms, "volume"),
           "pan": _env(ins.pan_envelope, tick_ms, "pan"),
           "pitch_or_filter": _env(ins.pitch_envelope, tick_ms,
                                   pitch_slot or "pitch"),
           "nna": NNA.get(ins.nna, ins.nna), "dct": DCT.get(ins.dct, ins.dct),
           "dca": DCA.get(ins.dca, ins.dca), "fadeout": ins.fadeout,
           "cutoff": ins.cutoff, "resonance": ins.resonance}
    notes = []
    vol = ins.volume_envelope
    if vol is not None and vol.enabled and not vol.sustain:
        notes.append("the volume envelope has no sustain loop: it plays out "
                     "the same whether the key is held or not; the key-off "
                     "only starts the fade-out")
    if ins.nna in (1, 2):
        notes.append(
            f"NNA {NNA[ins.nna]}: a new note on the channel sends this one to "
            f"the background, where it "
            + ("keeps playing as it was" if ins.nna == 1 else
               "gets a key-off and runs on through its envelope")
            + "; the next note does not cut it")
    elif ins.nna == 0:
        notes.append("NNA cut: the next note on the channel ends this one")
    if pitch_slot == "filter" and ins.cutoff is None:
        notes.append("a filter envelope with the filter off does nothing")
    slot = ins.pitch_envelope
    if slot is not None and not slot.enabled and slot.filter:
        notes.append("the pitch/filter slot holds a filter envelope that is "
                     "switched off but still marked as a filter: Schism "
                     "Tracker still lowers the filter, as if the envelope "
                     "stood at its middle value, and libopenmpt does not "
                     "(measured) — the two players will not agree")
    carried = [label for label, env in (("volume", ins.volume_envelope),
                                        ("pan", ins.pan_envelope),
                                        (pitch_slot or "pitch",
                                         ins.pitch_envelope))
               if env is not None and env.enabled
               and getattr(env, "carry", False)]
    if carried:
        notes.append(
            f"carry on the {' and '.join(carried)} envelope"
            f"{'s' if len(carried) > 1 else ''}: a note of this instrument "
            f"that follows one still held on the channel does not start "
            f"{'them' if len(carried) > 1 else 'it'} again, it goes on from "
            f"where that note is; after a key-off or a note cut (or a note "
            f"of another instrument) it starts again")
    out["notes"] = notes
    samples = sorted({s for _, s in ins.note_map if s})
    out["samples"] = []
    from . import phases
    for s in samples:
        smp = module.samples[s - 1]
        row = {"sample": s, "frames": smp.frames,
               "loop": list(smp.loop) if smp.loop else None,
               "sustain_loop": list(smp.sustain_loop)
               if smp.sustain_loop else None,
               "stereo": smp.right is not None,
               "auto_vibrato": [smp.vibrato_speed, smp.vibrato_depth,
                                smp.vibrato_rate, smp.vibrato_wave]}
        for key, span in (("loop_seam", smp.loop),
                          ("sustain_seam", smp.sustain_loop)):
            if span and smp.data is not None:
                row[key] = phases.loop_seam(smp.data, span[0], span[1])
        out["samples"].append(row)
    return out


def explain_patch(patch, tempo: int = 125) -> dict:
    """A forge patch's envelopes (written in seconds) on the tick grid of a
    tempo: where each node landed, how far it moved and which merged."""
    tick = 2.5 / tempo
    out = {"tempo": tempo, "tick_ms": round(tick * 1000.0, 3), "envelopes": {},
           "losses": []}
    for label, nodes, scale, low, high in (
            ("volume", patch.amp, 64.0, 0, 64),
            ("pan", patch.pan_env, 1.0, -32, 32),
            ("filter", patch.filter_env, 1.0, 0, 64),
            ("pitch", patch.pitch_env, 1.0, -32, 32)):
        if not nodes:
            continue
        realised, _ = patch_mod.to_ticks(nodes, tick, scale, low, high)
        rows, worst_ms, merged, worst_value = [], 0.0, 0, 0.0
        seen_ticks = {}
        for t, v in nodes:
            k = int(round(t / tick))
            moved = abs(k * tick - t) * 1000.0
            worst_ms = max(worst_ms, moved)
            asked = v * scale
            worst_value = max(worst_value, abs(round(asked) - asked))
            if k in seen_ticks:
                merged += 1
            seen_ticks[k] = v
            rows.append({"asked_ms": round(t * 1000.0, 2),
                         "tick": k, "asked": round(asked, 3)})
        out["envelopes"][label] = {"asked": rows,
                                   "file": [{"tick": k, "value": v}
                                            for k, v in realised]}
        if worst_ms > 1e-6:
            out["losses"].append({"kind": "clock", "target": label,
                                  "text": f"{label}: node times moved to the "
                                          f"{tick * 1000.0:g} ms tick",
                                  "size": round(worst_ms, 2)})
        if merged:
            out["losses"].append({"kind": "merged", "target": label,
                                  "text": f"{label}: {merged} node(s) landed "
                                          f"on a tick another node had; the "
                                          f"later one is in the file",
                                  "size": merged})
        if worst_value > 1e-9:
            out["losses"].append({"kind": "steps", "target": label,
                                  "text": f"{label}: values rounded to whole "
                                          f"steps", "size": round(worst_value,
                                                                  3)})
    if patch.filter_env and patch.pitch_env:
        out["losses"].append({"kind": "slot", "target": "pitch",
                              "text": "pitch and filter envelopes share one "
                                      "slot in the file: the patch is "
                                      "refused until one of them is removed",
                              "size": 1})
    return out
