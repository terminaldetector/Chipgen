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
    programs   a YM2612 instrument with a program (program.py) gets its
               per-note writes (modulator levels, feedback, pitch) at the
               60 Hz frame, its vibrato at each key-on, its gate, and, if
               it is legato, one key-on per run of touching notes with the
               pitch moved for the rest.

The pass reads the score's own events and leaves them alone except where
it replaces an NES note; it never moves a note. Where a layer would need a
channel the score already plays, the layer is dropped and the report says
so rather than the two parts fighting over one voice.
"""

import copy
from typing import Dict, List, Optional

import events as E

from . import nes_driver, registry
from . import program as program_mod
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
        self.program_notes = 0
        self.legato_notes = 0
        self.gated_notes = 0
        self.program_writes = 0
        self.warnings: List[str] = []

    def warn(self, text: str):
        if text not in self.warnings:
            self.warnings.append(text)

    def __bool__(self):
        return bool(self.layered_notes or self.macro_notes or self.settings
                    or self.program_notes or self.warnings)

    def text(self) -> str:
        parts = []
        if self.macro_notes:
            parts.append(f"{self.macro_notes} NES notes played from macros")
        if self.layered_notes:
            parts.append(f"{self.layered_notes} notes with a detune layer")
        if self.settings:
            parts.append(f"{self.settings} channel settings")
        if self.program_notes:
            parts.append(
                f"{self.program_notes} notes played from programs "
                f"({self.program_writes} writes"
                + (f", {self.legato_notes} legato" if self.legato_notes
                   else "")
                + (f", {self.gated_notes} gated" if self.gated_notes else "")
                + ")")
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
    """The events at their ticks, the score's length, and the ticks where
    the score's own Waits end. The rebuilt list cuts its time at those
    ticks too: the sequencer starts its effect clock afresh at every Wait,
    so a pass that merged the Waits of empty rows would move every
    arpeggio step and vibrato tick of the score — a score sounding
    otherwise because a forge instrument is installed somewhere."""
    items, cuts, now = [], [], 0
    for seq, ev in enumerate(events):
        if isinstance(ev, E.Wait):
            now += ev.ticks
            cuts.append(now)
            continue
        items.append(_Item(now, float(seq), ev, tps))
        if isinstance(ev, E.Tempo):
            tps = ev.ticks_per_second
    return items, now, cuts


def _rebuild(items, total, had_end, cuts=()):
    items = [it for it in items if not it.drop]
    items.sort(key=lambda it: (it.tick, it.order))
    out, now = [], 0
    last = max([total] + [it.tick for it in items
                          if not isinstance(it.event, E.End)])
    cuts = sorted(set(cuts))
    k = 0

    def wait_until(tick):
        nonlocal now, k
        while k < len(cuts) and cuts[k] <= now:
            k += 1
        while k < len(cuts) and cuts[k] < tick:
            out.append(E.Wait(ticks=cuts[k] - now))
            now = cuts[k]
            k += 1
        if tick > now:
            out.append(E.Wait(ticks=tick - now))
            now = tick

    for it in items:
        if isinstance(it.event, E.End):
            continue
        wait_until(it.tick)
        out.append(it.event)
    if had_end:
        wait_until(last)
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
    items, total, cuts = _timeline(events, tps)
    had_end = any(isinstance(it.event, E.End) for it in items)
    added: List[_Item] = []

    def add(tick, parent, offset_order, event, tps_):
        added.append(_Item(tick, parent.order + 0.001 * offset_order, event,
                           tps_))

    # programs first: a legato note they take the key-on from must not
    # re-key its detune layer either, and a gated key-off must end the layer
    _programs(items, total, added, add, report)
    _layers(items, total, added, add, report)
    _nes(items, total, added, add, report)
    if report.layer_voices:
        added.append(_Item(0, -1.0, E.Marker(
            label=LAYERS_MARK + " ".join(sorted(report.layer_voices))), tps))
    return _rebuild(items + added, total, had_end, cuts)


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
                if it.drop:
                    continue        # a legato note: no new key-on anywhere
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


# --------------------------------------------------------------------------
# Programs (YM2612)
# --------------------------------------------------------------------------
#: what in a score moves a voice's pitch: the program's pitch track and its
#: legato both drive the same Portamento, so a channel that has any of these
#: keeps the score's and the program's are left out there
_PITCH_EVENTS = (E.Portamento, E.Vibrato, E.Arpeggio)


def _pitch_owned(items) -> set:
    owned = set()
    for it in items:
        ev = it.event
        if isinstance(ev, _PITCH_EVENTS) and ev.target.startswith("fm") \
                and ev.target[2:].isdigit():
            owned.add(int(ev.target[2:]))
        elif isinstance(ev, E.FMPitch):
            owned.add(ev.channel)
    return owned


def _fm_window(items, index, channel, total):
    """-> (key-off tick or None, next note-on tick or None, stop tick) of
    the note items[index] starts: where the key comes up, and where the
    program stops (the next note, an instrument change, the end)."""
    key_off = None
    for later in items[index + 1:]:
        ev = later.event
        if later.drop and not isinstance(ev, E.FMNoteOn):
            continue
        if isinstance(ev, E.FMNoteOff) and ev.channel == channel:
            if key_off is None:
                key_off = later.tick
        elif isinstance(ev, E.FMNoteOn) and ev.channel == channel:
            return key_off, later.tick, later.tick
        elif isinstance(ev, E.FMInstrumentSelect) and ev.channel == channel:
            return key_off, None, later.tick
        elif isinstance(ev, E.End):
            return key_off, None, later.tick
    return key_off, None, total


def _semitones(note: str, octave: int) -> int:
    return E.NOTE_NAMES.index(note) + 12 * int(octave)


def _programs(items, total, added, add, report):
    """Every note of a YM2612 instrument that carries a program."""
    if not any(c.style.get("program") for (b, _), c in
               registry.FORGE.items() if b == "ym2612"):
        return
    owned = _pitch_owned(items)
    used = {it.event.channel for it in items
            if isinstance(it.event, E.FMNoteOn)}
    gated_offs: List[_Item] = []
    selected: Dict[int, str] = {}
    keyed: Dict[int, bool] = {}
    #: channel -> the run of touching notes a legato program is playing:
    #: {"root": semitone, "tick": key-on tick, "offset": cents, "name"}
    chains: Dict[int, dict] = {}
    vibrato_on: Dict[int, bool] = {}
    told = set()

    def warn_once(key, text):
        if key not in told:
            told.add(key)
            report.warn(text)

    for index, it in enumerate(items):
        ev = it.event
        if isinstance(ev, E.FMInstrumentSelect):
            selected[ev.channel] = ev.instrument
            chain = chains.pop(ev.channel, None)
            if vibrato_on.pop(ev.channel, False):
                add(it.tick, it, 1, E.Vibrato(target=f"fm{ev.channel}"),
                    it.tps)
            if chain and chain["offset"]:
                add(it.tick, it, 2, E.Portamento(
                    target=f"fm{ev.channel}",
                    cents_per_second=program_mod.JUMP_RATE, to_cents=0.0),
                    it.tps)
            continue
        if isinstance(ev, E.FMNoteOff):
            keyed[ev.channel] = False
            continue
        if not isinstance(ev, E.FMNoteOn):
            continue
        channel = ev.channel
        was_keyed = keyed.get(channel, False)
        keyed[channel] = True
        name = selected.get(channel)
        compiled = registry.lookup("ym2612", name) if name else None
        program = program_mod.of(compiled) if compiled is not None else None
        if program is None:
            chain = chains.pop(channel, None)
            if chain and chain["offset"]:
                add(it.tick, it, -0.5, E.Portamento(
                    target=f"fm{channel}",
                    cents_per_second=program_mod.JUMP_RATE, to_cents=0.0),
                    it.tps)
            continue
        tps = it.tps
        key_off, next_on, stop = _fm_window(items, index, channel, total)
        pitched_ok = channel not in owned
        if not pitched_ok and (program.articulation.mode == "legato"
                               or program.track("pitch") or program.vibrato):
            warn_once(("pitch", channel),
                      f"fm{channel}: the score moves this channel's pitch "
                      f"(porta, vib, arp or pitch), so {name}'s pitch track, "
                      f"vibrato and legato are left out there")
        semis = _semitones(ev.note, ev.octave)
        # the detune layers this instrument plays, where they are free
        layers = [(channel + k, layer) for k, layer in
                  enumerate(compiled.style.get("layers", ()), 1)]
        if any(c in used or c >= 6 for c, _ in layers):
            layers = []
        chain = chains.get(channel)
        legato = (program.articulation.mode == "legato" and pitched_ok
                  and was_keyed and chain is not None
                  and chain["name"] == name)
        # the score's own writes to what the program writes, inside the note
        skip, back = {}, []
        for later in items[index + 1:]:
            if later.tick >= stop:
                break
            lev = later.event
            reg = None
            if isinstance(lev, E.FMOperator) and lev.channel == channel \
                    and lev.field == "tl":
                reg = f"op{lev.operator}.tl"
            elif isinstance(lev, E.FMAlgorithm) and lev.channel == channel \
                    and lev.feedback is not None:
                reg = "fb"
            if reg is None:
                continue
            track = _track_for(program, reg)
            if track is None:
                continue
            if track.priority == "pattern":
                skip.setdefault(reg, later.tick - it.tick)
            else:
                back.append((later, reg))
            warn_once(("conflict", channel, reg),
                      f"fm{channel}: the score writes {reg} inside a note of "
                      f"{name}, whose program writes it too; "
                      + ("the score's value stands until the next note"
                         if track.priority == "pattern" else
                         "the program's value is written back after it"))
        if legato:
            # no new attack: the pitch moves and the program runs on
            it.drop = True
            offset = 100.0 * (semis - chain["root"])
            glide = program.articulation.glide_ms
            if glide > 0:
                rate = max(1.0, abs(offset - chain["offset"])
                           / (glide / 1000.0))
            else:
                rate = program_mod.JUMP_RATE
            add(it.tick, it, 1, E.Portamento(target=f"fm{channel}",
                                             cents_per_second=rate,
                                             to_cents=offset), tps)
            for c, layer in layers:
                add(it.tick, it, 1.5, E.Portamento(
                    target=f"fm{c}", cents_per_second=rate,
                    to_cents=offset + layer["cents"]), tps)
            chain["offset"] = offset
            start_frame = int(round((it.tick - chain["tick"]) / tps
                                    * program_mod.FRAME_RATE))
            pitch_base = offset
            report.legato_notes += 1
        else:
            old = chains.get(channel)
            if old and old["offset"]:
                add(it.tick, it, -0.5, E.Portamento(
                    target=f"fm{channel}",
                    cents_per_second=program_mod.JUMP_RATE, to_cents=0.0),
                    tps)
            chain = chains[channel] = {"root": semis, "tick": it.tick,
                                       "offset": 0.0, "name": name}
            start_frame = 0
            pitch_base = 0.0
            if program.vibrato and pitched_ok:
                v = program.vibrato
                for c in [channel] + [c for c, _ in layers]:
                    add(it.tick, it, 1, E.Vibrato(
                        target=f"fm{c}", depth_cents=v["depth_cents"],
                        speed_hz=v["speed_hz"],
                        delay=v["delay_ms"] / 1000.0), tps)
                vibrato_on[channel] = True
        # where the key comes up: the score's key-off, or the gate
        gate = program.articulation.gate
        ends_run = key_off is not None or program.articulation.mode != "legato"
        off = key_off
        if gate < 1.0 and ends_run:
            written = (key_off if key_off is not None else
                       next_on if next_on is not None else stop) - it.tick
            gated = it.tick + max(1, int(round(written * gate)))
            if key_off is None or gated < key_off:
                gated_offs.append(_Item(gated, it.order + 0.05,
                                        E.FMNoteOff(channel=channel), tps))
                off = gated
                report.gated_notes += 1
        note = program_mod.fm_note(
            program, compiled.patch, channel, tps,
            None if off is None else off - it.tick, stop - it.tick,
            pitch_base=pitch_base, start_frame=start_frame, skip=skip,
            pitch_allowed=pitched_ok)
        for tick, order, extra in note.events:
            add(it.tick + tick, it, 2 + order * 0.01, extra, tps)
            report.program_writes += 1
        for later, reg in back:
            value = program_mod.value_at_tick(note, reg,
                                              later.tick - it.tick, tps)
            if value is not None:
                added.append(_Item(later.tick, later.order + 0.0005,
                                   program_mod.native_event(channel, reg,
                                                            value), tps))
        report.program_notes += 1
    if gated_offs:
        # into the timeline, not beside it: the layer pass reads note ends
        # from the timeline, and a gated note's layer must stop with it
        items.extend(gated_offs)
        items.sort(key=lambda it: (it.tick, it.order))


def _track_for(program, register):
    """The program track that writes a native register, or None."""
    if register == "fb":
        return program.track("fb")
    track = program.track(register)
    return track if track is not None else program.track("mod.tl")


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
        registered = registry.lookup("nes", inst.name)
        gate = registered.style.get("gate", 1.0) if registered else 1.0
        for j, (offset, extra) in enumerate(nes_driver.to_events(
                playing, voice, note, octave, max(1, end - it.tick), velocity,
                it.tps, gate), 1):
            add(it.tick + offset, it, j, extra, it.tps)
        report.macro_notes += 1
