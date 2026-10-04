"""placement.py — put forge instruments into a score the engine can play.

The engine's banks hold a patch. A forge instrument can be more: a detune
layer (the same note on a second channel, a few cents sharp), a channel
setting (the YM2612's LFO depth, the OPL2's vibrato depth), or on the NES,
no patch at all but a handful of macros that a driver plays one frame at a
time. This module turns those into ordinary events, once, when the score is
parsed, so that the sequencer, the VGM writer and everything after them
know nothing about the forge:

    FM / OPL   `inst fm0 lead` and `inst opl2 pad` already name a bank
               entry. If the entry came from the forge and has a layer or
               a channel setting, the extra events are added next to the
               ones the score already has.
    NES        `inst nes0 lead` has nothing to select, so the tracker
               leaves a marker and this pass expands every note on that
               voice through nes_driver (volume, duty, pitch and arpeggio
               macros as the engine's own NESVolume/NESDuty/Portamento
               events, at the frame rate).

The pass reads the score's own events and leaves them alone except where
it replaces an NES note; it never moves a note. Where a layer would need a
channel the score already plays, the layer is dropped and the report says
so rather than the two parts fighting over one voice.
"""

import copy
from typing import Dict, List, Optional

import events as E

from . import nes_driver, registry
from .backends import get_backend

#: the label the tracker's `inst nes0 NAME` leaves in the event stream
MARK = "forge:nes "
#: the label this pass leaves, once, at the start of a score that got
#: detune layers: the channels that double another part and are not parts
#: of their own (sanity.py reads it, so a layered lead is not "crowding")
LAYERS_MARK = "forge:layers "


class PlacementError(ValueError):
    """An instrument that cannot be used where the score asks for it."""


class Report:
    def __init__(self):
        self.layer_voices = set()
        self.layered_notes = 0
        self.macro_notes = 0
        self.settings = 0
        self.warnings: List[str] = []

    def warn(self, text: str):
        if text not in self.warnings:
            self.warnings.append(text)

    def __bool__(self):
        return bool(self.layered_notes or self.macro_notes or self.settings
                    or self.warnings)

    def text(self) -> str:
        parts = []
        if self.macro_notes:
            parts.append(f"{self.macro_notes} NES notes played from macros")
        if self.layered_notes:
            parts.append(f"{self.layered_notes} notes with a detune layer")
        if self.settings:
            parts.append(f"{self.settings} channel settings")
        return "; ".join(parts + self.warnings) or "nothing to place"


#: the report of the most recent expand(), for callers that want to show it
LAST = Report()


def label_for(voice: str, name: str, line: int = 0) -> str:
    return f"{MARK}{voice} {name}" + (f" @{line}" if line else "")


def _parse_label(label: str):
    body = label[len(MARK):].split()
    voice, name = body[0], body[1]
    line = int(body[2][1:]) if len(body) > 2 and body[2].startswith("@") else 0
    return voice, name, line


def needs_expansion(events) -> bool:
    if registry.needs_expansion():
        return True
    return any(isinstance(ev, E.Marker) and ev.label.startswith(MARK)
               for ev in events)


# --------------------------------------------------------------------------
# The timeline: events with absolute ticks, and back to Waits
# --------------------------------------------------------------------------
class _Item:
    __slots__ = ("tick", "order", "event", "tps", "drop")

    def __init__(self, tick, order, event, tps):
        self.tick, self.order, self.event, self.tps = tick, order, event, tps
        self.drop = False


def _timeline(events, tps):
    items, now = [], 0
    for seq, ev in enumerate(events):
        if isinstance(ev, E.Wait):
            now += ev.ticks
            continue
        items.append(_Item(now, float(seq), ev, tps))
        if isinstance(ev, E.Tempo):
            tps = ev.ticks_per_second
    return items, now


def _rebuild(items, total, had_end):
    items = [it for it in items if not it.drop]
    items.sort(key=lambda it: (it.tick, it.order))
    out, now = [], 0
    last = max([total] + [it.tick for it in items
                          if not isinstance(it.event, E.End)])
    for it in items:
        if isinstance(it.event, E.End):
            continue
        if it.tick > now:
            out.append(E.Wait(ticks=it.tick - now))
            now = it.tick
        out.append(it.event)
    if had_end:
        if last > now:
            out.append(E.Wait(ticks=last - now))
        out.append(E.End())
    return out


def _note_end(items, index, is_off, is_next_on, total) -> int:
    """The tick at which the note started by items[index] ends: its own
    off, or the next on for the same voice, or the end of the score."""
    for later in items[index + 1:]:
        if later.drop:
            continue
        if is_off(later.event) or is_next_on(later.event):
            return later.tick
        if isinstance(later.event, E.End):
            return later.tick
    return total


# --------------------------------------------------------------------------
# The pass
# --------------------------------------------------------------------------
def expand(events, meta=None) -> list:
    """Return `events` with forge instruments placed. The input list is
    not modified. See the module docstring for what this adds."""
    global LAST
    report = LAST = Report()
    if not needs_expansion(events):
        return events
    tps = getattr(meta, "ticks_per_second", None) or 192.0
    items, total = _timeline(events, tps)
    had_end = any(isinstance(it.event, E.End) for it in items)
    added: List[_Item] = []

    def add(tick, parent, offset_order, event, tps_):
        added.append(_Item(tick, parent.order + 0.001 * offset_order, event,
                           tps_))

    _layers(items, total, added, add, report)
    _nes(items, total, added, add, report)
    if report.layer_voices:
        added.append(_Item(0, -1.0, E.Marker(
            label=LAYERS_MARK + " ".join(sorted(report.layer_voices))), tps))
    return _rebuild(items + added, total, had_end)


#: events aimed at a part by name ("fm2", "opl3") that a detune layer
#: should hear too: a layer is the same part, not a second one. Pitch
#: effects (porta, pitch, arp) are not here: the layer's own pitch offset
#: is how it is detuned, and a second offset would replace it.
_MIRRORED_EFFECTS = (E.Vibrato, E.Tremolo, E.VolumeSlide)


def _layers(items, total, added, add, report):
    """FM and OPL: channel settings at each select, layers at each note,
    and whatever the part is told (volume, pan, vibrato...) passed on to
    its layers."""
    chips = (
        ("ym2612", "fm", E.FMInstrumentSelect, E.FMNoteOn, E.FMNoteOff,
         E.FMVolume, 6),
        ("opl2", "opl", E.OPLInstrumentSelect, E.OPLNoteOn, E.OPLNoteOff,
         E.OPLVolume, 9),
    )
    for backend_name, prefix, Select, NoteOn, NoteOff, Volume, count in chips:
        used = {it.event.channel for it in items
                if isinstance(it.event, NoteOn)}
        selected: Dict[int, str] = {}
        pans: Dict[int, E.FMPan] = {}      # channel -> its last FMPan
        volumes: Dict[int, object] = {}    # channel -> its last volume event
        backend = None

        def wanted(channel):
            """The layer channels the instrument selected on `channel`
            has, whether or not they are free."""
            compiled = registry.lookup(backend_name, selected.get(channel, ""))
            if compiled is None:
                return []
            return [channel + k for k in
                    range(1, len(compiled.style.get("layers", ())) + 1)]

        def free(channel):
            """...and the ones that are."""
            chans = wanted(channel)
            if any(c in used or c >= count for c in chans):
                return []
            return chans

        for index, it in enumerate(items):
            ev = it.event
            n = 0

            def put(event, tick=None):
                nonlocal n
                n += 1
                add(it.tick if tick is None else tick, it, n, event, it.tps)

            if backend_name == "ym2612" and isinstance(ev, E.FMPan):
                compiled = registry.lookup(backend_name,
                                           selected.get(ev.channel, ""))
                # a `pan` line carries ams/pms 0 unless it says otherwise,
                # which would switch off the instrument's LFO depth
                if compiled is not None and compiled.setup \
                        and not ev.ams and not ev.pms:
                    ev = it.event = ev.copy_with(
                        ams=compiled.setup.get("ams", 0),
                        pms=compiled.setup.get("pms", 0))
                pans[ev.channel] = ev
                for c in free(ev.channel):
                    put(ev.copy_with(channel=c))
                continue
            if isinstance(ev, Volume):
                volumes[ev.channel] = ev
                for c in free(ev.channel):
                    put(ev.copy_with(channel=c))
                continue
            if isinstance(ev, _MIRRORED_EFFECTS) \
                    and ev.target.startswith(prefix) \
                    and ev.target[len(prefix):].isdigit():
                for c in free(int(ev.target[len(prefix):])):
                    put(ev.copy_with(target=f"{prefix}{c}"))
                continue
            if isinstance(ev, Select):
                selected[ev.channel] = ev.instrument
                compiled = registry.lookup(backend_name, ev.instrument)
                if compiled is None or not (compiled.setup
                                            or compiled.style.get("layers")):
                    continue
                backend = backend or get_backend(backend_name)
                layer_free = free(ev.channel)
                panned = set()
                for extra in backend.setup_events(compiled, ev.channel):
                    channel = getattr(extra, "channel", ev.channel)
                    if isinstance(extra, Select) and channel == ev.channel:
                        continue                     # the score's own
                    if channel != ev.channel and channel not in layer_free:
                        continue                     # the layer is dropped below
                    if isinstance(extra, E.FMPan):
                        parent = pans.get(ev.channel)
                        if parent is not None:        # keep the part's panning
                            extra = extra.copy_with(left=parent.left,
                                                    right=parent.right)
                    if isinstance(extra, E.FMPan):
                        panned.add(channel)
                    put(extra)
                    report.settings += 1
                parent_pan = pans.get(ev.channel)
                if parent_pan is not None:       # a pan said before the select
                    for c in layer_free:
                        if c not in panned:
                            put(parent_pan.copy_with(channel=c))
                parent_volume = volumes.get(ev.channel)
                if parent_volume is not None:
                    for c in layer_free:
                        put(parent_volume.copy_with(channel=c))
            elif isinstance(ev, NoteOn):
                name = selected.get(ev.channel)
                compiled = registry.lookup(backend_name, name) if name else None
                if compiled is None or not compiled.style.get("layers"):
                    continue
                chans = wanted(ev.channel)
                blocked = [c for c in chans if c in used or c >= count]
                if blocked:
                    report.warn(
                        f"{name} wants {'/'.join(str(c) for c in chans)} for "
                        f"its detune layer but the score plays or lacks "
                        f"channel {blocked[0]}; layer dropped (use a free "
                        f"channel above this one, or set detune 0)")
                    continue
                end = _note_end(
                    items, index,
                    lambda e, c=ev.channel: isinstance(e, NoteOff)
                    and e.channel == c,
                    lambda e, c=ev.channel: isinstance(e, NoteOn)
                    and e.channel == c, total)
                backend = backend or get_backend(backend_name)
                length = max(1, end - it.tick)
                report.layer_voices.update(f"{prefix}{c}" for c in chans)
                for offset, extra in backend.layer_events(
                        compiled, ev.channel, ev.note, ev.octave, ev.velocity,
                        length):
                    put(extra, it.tick + offset)
                report.layered_notes += 1


_NES_VOICES = ("pulse1", "pulse2", "triangle", "noise")


def _nes(items, total, added, add, report):
    """NES: expand each note of a voice that has a macro instrument."""
    markers = [it for it in items if isinstance(it.event, E.Marker)
               and it.event.label.startswith(MARK)]
    if not markers:
        return
    used_voices = {it.event.voice for it in items
                   if isinstance(it.event, E.NESNoteOn)}
    current: Dict[str, nes_driver.NESInstrument] = {}
    for index, it in enumerate(items):
        ev = it.event
        if isinstance(ev, E.Marker) and ev.label.startswith(MARK):
            voice, name, line = _parse_label(ev.label)
            try:
                inst = nes_driver.get(name)
            except KeyError as error:
                raise PlacementError(
                    (f"line {line}: " if line else "") + str(error.args[0])
                    + ". Load a bank with --forge-bank (or "
                      "synthesis.bank.Bank.install) before the score is "
                      "parsed.") from None
            want = {"pulse1": "pulse", "pulse2": "pulse",
                    "triangle": "triangle", "noise": "noise"}[voice]
            if inst.channel != want:
                raise PlacementError(
                    (f"line {line}: " if line else "")
                    + f"{name} is a {inst.channel} instrument and {voice} is "
                      f"a {want} channel; pick a {want} instrument for it "
                      f"(`forge.py show BANK` lists each one's channel)")
            current[voice] = inst
            it.drop = True
            continue
        if isinstance(ev, E.NESNoteOn):
            voice, note, octave = ev.voice, ev.note, ev.octave
            is_off = lambda e, v=voice: isinstance(e, E.NESNoteOff) \
                and e.voice == v
            is_on = lambda e, v=voice: isinstance(e, E.NESNoteOn) \
                and e.voice == v
        elif isinstance(ev, E.NESNoiseOn):
            voice, note, octave = "noise", "C", 4
            is_off = lambda e: isinstance(e, E.NESNoiseOff)
            is_on = lambda e: isinstance(e, E.NESNoiseOn)
        else:
            continue
        inst = current.get(voice)
        if inst is None:
            continue
        end = _note_end(items, index, is_off, is_on, total)
        for later in items[index + 1:]:
            if later.tick > end:
                break
            if is_off(later.event):
                later.drop = True
                break
        it.drop = True
        playing = inst
        if inst.layers and "pulse2" in used_voices and voice == "pulse1" \
                or inst.layers and "pulse1" in used_voices and voice == "pulse2":
            playing = copy.copy(inst)
            playing.layers = []
            report.warn(f"{inst.name} wants the other pulse channel for its "
                        f"detune layer but the score plays it; layer dropped")
        velocity = getattr(ev, "velocity", 127)
        if playing.layers:
            report.layer_voices.add("pulse2" if voice == "pulse1"
                                    else "pulse1")
        for j, (offset, extra) in enumerate(nes_driver.to_events(
                playing, voice, note, octave, max(1, end - it.tick), velocity,
                it.tps), 1):
            add(it.tick + offset, it, j, extra, it.tps)
        report.macro_notes += 1
