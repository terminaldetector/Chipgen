"""
it_export.py — write the performance as an Impulse Tracker `.it` module.

chipgen already emits two things you can listen to: a WAV, and a VGM
register log. Neither is editable. A `.it` is: Schism Tracker, OpenMPT and
every module player on earth open it, and a human can move a note, fix a
bar, and re-export. That closes the loop this project is actually about —
a model writes the track, a person finishes it.

The trade is honest and worth stating plainly, because it is not a
lossless conversion:

  * IT is SAMPLE-based; the YM2612 is not. So each patch is rendered
    through the real emulator at one reference pitch and stored as a
    looped sample. Transposing a sample is not the same as retuning an FM
    operator — key scaling, fixed-frequency operators and detune all shift
    the timbre across the keyboard on real hardware, and a sample cannot
    follow that. Near the reference octave it is very close; four octaves
    up it is a different instrument.

  * A tracker channel plays one sample. The DAC column becomes an ordinary
    sample channel, which is what a Genesis driver is doing anyway.

  * IT's timing grid is coarser than the engine's. See `_it_timing`.

What is NOT lossy: the notes, their order, the velocities, the panning,
and the drums. Those are the arrangement, and they survive exactly.
"""

import struct

import events as events_mod
import instruments as instruments_mod
import samples as samples_mod

#: IT stores note 0 as C-0, and chipgen counts octaves the same way (its
#: octave 4 holds A440, so C-5 is 60 semitones up from C-0 — the same 60
#: IT writes). That coincidence means no transposition on export.
IT_NOTE_OFF = 255
IT_NOTE_CUT = 254

#: Rendered at C-5 (523.25 Hz), the middle of where these patches are
#: actually played. A sample is most faithful near the pitch it was taken
#: at, so the reference note wants to sit inside the music, not below it.
REFERENCE_NOTE = ("C", 5)
SAMPLE_RATE = 44100

#: IT's own limits, from the format: tempo is a byte clamped to 32-255 and
#: speed is ticks-per-row, at least 1. One tick lasts 2.5/tempo seconds.
IT_MIN_TEMPO, IT_MAX_TEMPO = 32, 255
IT_ROWS_PER_PATTERN = 64
#: Ticks per row to aim for. IT's own default, and the point below which
#: its arpeggio effect has no room to cycle.
PREFERRED_SPEED = 6
IT_MAX_ROWS = 200

#: Which IT channel each chipgen voice lands on. Ordered so the FM block,
#: the PSG block and the drums stay visually grouped in a tracker window.
CHANNEL_MAP = {}
for _i in range(6):
    CHANNEL_MAP[("fm", _i)] = _i          # IT channels 1-6
for _i in range(3):
    CHANNEL_MAP[("psg", _i)] = 6 + _i     # 7-9
CHANNEL_MAP[("noise", 0)] = 9             # 10
CHANNEL_MAP[("dac", 0)] = 10              # 11
for _i in range(9):
    CHANNEL_MAP[("opl", _i)] = 11 + _i    # 12-20
IT_CHANNELS_USED = 20


class ITExportError(ValueError):
    pass


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------
def _it_timing(row_seconds: float):
    """Pick (speed, tempo) for a row of `row_seconds`, and say how close.

    IT runs on ticks of 2.5/tempo seconds and spends `speed` of them per
    row, so a row can only last speed*2.5/tempo. Both are integers, tempo
    is capped at 255, and the shortest representable row is therefore
    2.5/255 = 9.8ms. Returns (speed, tempo, error_seconds) — the caller
    decides whether the error matters rather than this silently rounding.
    """
    best = None
    for tempo in range(IT_MIN_TEMPO, IT_MAX_TEMPO + 1):
        speed = round(row_seconds * tempo / 2.5)
        if speed < 1 or speed > 255:
            continue
        error = abs(speed * 2.5 / tempo - row_seconds)
        # Among equally exact answers, prefer speed 6. Two reasons, both
        # practical: it is the tracker default, so `tempo` comes out equal
        # to the actual BPM and a human editing the file sees the number
        # they expect; and a row needs at least three ticks before IT's
        # arpeggio effect has anywhere to put its second and third step.
        score = (round(error, 9), abs(speed - PREFERRED_SPEED))
        if best is None or score < best[0]:
            best = (score, speed, tempo, error)
    if best is not None:
        return best[1], best[2], best[3]
    if best is None:
        raise ITExportError(
            f"a row of {row_seconds*1000:.2f}ms cannot be expressed in IT "
            f"(the format's floor is {2.5/IT_MAX_TEMPO*1000:.1f}ms) — "
            f"lower `lpb` or the tempo")


# --------------------------------------------------------------------------
# Turning a chip voice into a sample
# --------------------------------------------------------------------------
def _mono16(frames, gain: float = 1.0):
    """Chip output -> signed 16-bit mono.

    The FM core hands back stereo pairs and the PSG hands back bare
    scalars, so the shape is settled once from the first frame rather than
    per sample — and by asking for a length, because indexing a numpy
    scalar raises IndexError while indexing a float raises TypeError, and
    catching only one of those was this function's first bug.
    """
    if len(frames) == 0:
        return b""
    try:
        stereo = len(frames[0]) >= 2
    except TypeError:
        stereo = False

    out = bytearray()
    for frame in frames:
        value = (frame[0] + frame[1]) * 0.5 if stereo else frame
        value = int(round(max(-1.0, min(1.0, float(value) * gain)) * 32767))
        out += struct.pack("<h", value)
    return bytes(out)


#: Frames per waveform period in a rendered sample. The loop is built as a
#: whole number of these, so it is exactly periodic by construction.
FRAMES_PER_PERIOD = 128
#: How many periods the loop spans. More than one so that a patch whose
#: operators beat against each other keeps some of that movement.
LOOP_PERIODS = 8


def _sample_rate_for(frequency: float) -> float:
    """Render rate that makes one period exactly FRAMES_PER_PERIOD frames."""
    return frequency * FRAMES_PER_PERIOD


def _c5speed(render_rate: float, note: str, octave: int) -> int:
    """Playback rate at which this sample sounds at C-5.

    IT tunes a sample by naming the rate that makes it play C-5, so the
    rendered note does not have to BE C-5 — it only has to be known. That
    is what lets the render rate be chosen for an exact loop instead of
    for a round number.
    """
    midi = octave * 12 + events_mod.NOTE_NAMES.index(note)
    return max(1, int(round(render_rate * 2.0 ** ((60 - midi) / 12.0))))


def render_fm_sample(instrument, attack: float = 0.25, note=REFERENCE_NOTE):
    """Render one FM patch through the real emulator into an IT sample.

    The sample is attack + an exactly-periodic loop. `attack` is how much
    of the patch's own onset is kept verbatim before the loop takes over;
    a patch that is still moving after that has its movement frozen, which
    is the price of turning a synthesiser into a sampler.
    """
    import opn2

    chip = opn2.YM2612()
    chip.set_instrument(0, instrument)
    chip.set_pan(0, True, True)
    chip.note_on(0, note[0], note[1])
    native = chip.native_rate
    # What the chip WILL play, not what was asked for: the F-number is
    # quantised, and a loop built on the requested frequency would be a
    # fraction of a frame out on every repeat.
    fnum, block = opn2.freq_to_fnum_block(
        _note_frequency(note[0], note[1]), chip.clock)
    actual = opn2.fnum_block_to_freq(fnum, block, chip.clock)

    rate = _sample_rate_for(actual)
    loop_seconds = LOOP_PERIODS / actual
    body = chip.render(int(native * (attack + loop_seconds + 0.05)))
    chip.close()
    return _build(body, native, rate, attack, note, actual)


def render_opl_sample(instrument, attack: float = 0.25, note=REFERENCE_NOTE):
    """The same construction as render_fm_sample, on the other chip.

    The OPL2's pitch is quantised by a ten-bit F-Number just as the
    YM2612's is by its own, so the loop is built on what the chip will
    play rather than on what was asked for.
    """
    import opl2

    chip = opl2.YM3812()
    chip.set_instrument(0, instrument)
    chip.note_on(0, note[0], note[1])
    native = chip.native_rate
    fnum, block = opl2.freq_to_fnum_block(
        _note_frequency(note[0], note[1]), chip.clock)
    actual = opl2.fnum_block_to_freq(fnum, block, chip.clock)

    rate = _sample_rate_for(actual)
    body = chip.render(int(native * (attack + LOOP_PERIODS / actual + 0.05)))
    chip.close()
    return _build(body, native, rate, attack, note, actual)


def render_psg_tone_sample(note=REFERENCE_NOTE, attack: float = 0.02):
    """A PSG square, rendered by the PSG rather than synthesised by hand.

    The point of doing it this way is the same as everywhere else in this
    project: the sample carries the chip's real duty cycle and its real
    quantised pitch, not an idealised square. Above about C6 the 10-bit
    tone register makes neighbouring semitones collide, and this sample
    inherits that — correctly.
    """
    import sn76489

    chip = sn76489.SN76489()
    chip.tone_on(0, note[0], note[1], 0)
    native = chip.native_rate
    actual = sn76489.tone_n_to_freq(
        sn76489.freq_to_tone_n(_note_frequency(note[0], note[1]), chip.clock),
        chip.clock)

    rate = _sample_rate_for(actual)
    body = chip.render(int(native * (attack + LOOP_PERIODS / actual + 0.05)))
    chip.close()
    return _build(body, native, rate, attack, note, actual)


def render_psg_noise_sample(white: bool = True, rate: int = 1,
                            hold: float = 0.5):
    import sn76489

    chip = sn76489.SN76489()
    chip.noise_on(white, rate, 0)
    native = chip.native_rate
    body = chip.render(int(native * hold))
    chip.close()

    import audio
    resampled = audio.resample(body, native, SAMPLE_RATE)
    # Deliberately a one-shot. Noise has no period, so any loop repeats
    # audibly and stops sounding like noise — the exact failure this
    # project already fixed once, in the PSG's own LFSR reset.
    return {"data": _mono16(resampled), "loop": None,
            "c5speed": SAMPLE_RATE, "frames": len(resampled),
            "frequency": 0.0}


def _note_frequency(note: str, octave: int) -> float:
    midi = octave * 12 + events_mod.NOTE_NAMES.index(note)
    return 440.0 * 2.0 ** ((midi - 57) / 12.0)      # octave 4 holds A440


def _build(frames, native_rate: float, rate: float, attack: float,
           note, frequency: float):
    """Resample to `rate`, then cut a loop of whole periods out of the tail."""
    import audio

    resampled = audio.resample(frames, native_rate, rate)
    pcm = _mono16(resampled)
    total = len(pcm) // 2

    loop_len = FRAMES_PER_PERIOD * LOOP_PERIODS
    start = int(attack * rate)
    fade = min(loop_len // 4, start)
    if total < start + loop_len:
        # Too short to loop — keep it as a one-shot rather than inventing
        # a loop that would not be periodic.
        loop = None
    else:
        pcm = _crossfade_loop(pcm, start, start + loop_len, fade)
        loop = (start, start + loop_len)
        total = start + loop_len

    return {"data": pcm, "loop": loop, "frames": total,
            "c5speed": _c5speed(rate, note[0], note[1]),
            "frequency": frequency}


def _crossfade_loop(pcm16: bytes, start: int, end: int, fade: int) -> bytes:
    """Blend the loop's tail into the material before its head, then cut.

    A loop of whole fundamental periods is exact only if every partial is
    a harmonic. FM detune deliberately makes operators inharmonic — two of
    this project's own patches measured 45-49% waveform difference across
    one loop length with their envelopes flat, which is a discontinuity at
    every wrap and a buzz at the loop rate. Crossfading is what samplers
    have always done about this: the seam stops clicking, at the cost of
    softening the few milliseconds around it.
    """
    if fade <= 0:
        return pcm16[:end * 2]
    total = len(pcm16) // 2
    values = list(struct.unpack_from(f"<{total}h", pcm16))
    for i in range(fade):
        t = (i + 1) / float(fade + 1)
        tail = values[end - fade + i]
        # The frames immediately before the loop start are, by definition,
        # what the ear expects to hear leading into it.
        lead = values[start - fade + i]
        values[end - fade + i] = int(round(tail * (1.0 - t) + lead * t))
    return struct.pack(f"<{end}h", *values[:end])


def dac_sample(name: str):
    """A kit sample, converted from the DAC's unsigned bytes to signed 16."""
    kit = samples_mod.KIT[name]
    out = bytearray()
    for raw in kit.data:
        out += struct.pack("<h", max(-32768, min(32767, (raw - 128) * 256)))
    return {"data": bytes(out), "loop": None, "c5speed": kit.rate,
            "frames": len(kit.data)}


# --------------------------------------------------------------------------
# The event list as a grid of tracker rows
# --------------------------------------------------------------------------
class _Cell:
    __slots__ = ("note", "instrument", "volume", "effect", "param")

    def __init__(self):
        self.note = 0            # 0 = empty, 1-120 = C-0..B-9 (+1, see _pack)
        self.instrument = 0
        self.volume = -1         # -1 = empty, 0-64 = volume, 128-192 = pan
        self.effect = 0
        self.param = 0

    def empty(self) -> bool:
        return not (self.note or self.instrument or self.effect or self.param
                    or self.volume >= 0)


def _fm_volume(velocity: int) -> int:
    return max(0, min(64, int(round(velocity * 64.0 / 127.0))))


def _psg_volume(volume: int) -> int:
    """PSG attenuators run backwards: 0 is loudest, 15 is silence."""
    return max(0, min(64, int(round((15 - volume) * 64.0 / 15.0))))


def _note_byte(note: str, octave: int) -> int:
    """chipgen (name, octave) -> IT note, one-based for the packer.

    IT stores C-0 as 0 and chipgen numbers octaves the same way, so the
    only adjustment is the packer's own +1 convention.
    """
    value = octave * 12 + events_mod.NOTE_NAMES.index(note)
    if not 0 <= value <= 119:
        raise ITExportError(
            f"{note}-{octave} is outside IT's C-0..B-9 range")
    return value + 1


class _Grid:
    """Rows of cells, grown on demand, indexed by (row, it_channel)."""

    def __init__(self, channels: int):
        self.channels = channels
        self.rows = []

    def cell(self, row: int, channel: int) -> _Cell:
        while len(self.rows) <= row:
            self.rows.append([_Cell() for _ in range(self.channels)])
        return self.rows[row][channel]

    def __len__(self):
        return len(self.rows)


def build_grid(events, ticks_per_row: int, sample_index):
    """Walk the event list into a tracker grid.

    `sample_index` maps a voice to its IT sample number — see
    `collect_samples`, which decides what needs rendering in the first
    place. Returns (grid, skipped) where `skipped` counts events the
    format cannot carry, so the caller can report them instead of this
    quietly dropping them.
    """
    grid = _Grid(IT_CHANNELS_USED)
    skipped = {}
    tick = 0
    fm_patch = {}
    opl_patch = {}

    def note(count):
        skipped[count] = skipped.get(count, 0) + 1

    for event in events:
        E = events_mod
        if isinstance(event, E.Wait):
            tick += event.ticks
            continue
        if isinstance(event, E.End):
            break
        row = tick // ticks_per_row

        if isinstance(event, E.FMInstrumentSelect):
            fm_patch[event.channel] = event.instrument
        elif isinstance(event, E.FMNoteOn):
            cell = grid.cell(row, CHANNEL_MAP[("fm", event.channel)])
            cell.note = _note_byte(event.note, event.octave)
            cell.instrument = sample_index.get(("fm", fm_patch.get(event.channel)), 0)
            cell.volume = _fm_volume(event.velocity)
        elif isinstance(event, E.FMNoteOff):
            grid.cell(row, CHANNEL_MAP[("fm", event.channel)]).note = IT_NOTE_OFF
        elif isinstance(event, E.OPLInstrumentSelect):
            opl_patch[event.channel] = event.instrument
        elif isinstance(event, E.OPLNoteOn):
            cell = grid.cell(row, CHANNEL_MAP[("opl", event.channel)])
            cell.note = _note_byte(event.note, event.octave)
            cell.instrument = sample_index.get(
                ("opl", opl_patch.get(event.channel)), 0)
            cell.volume = _fm_volume(event.velocity)
        elif isinstance(event, E.OPLNoteOff):
            grid.cell(row, CHANNEL_MAP[("opl", event.channel)]).note = IT_NOTE_OFF
        elif isinstance(event, E.OPLVolume):
            cell = grid.cell(row, CHANNEL_MAP[("opl", event.channel)])
            cell.effect = ord("M") - ord("A") + 1
            cell.param = max(0, min(64, int(round(event.volume * 64.0 / 127.0))))
        elif isinstance(event, E.OPLDepth):
            # The OPL's tremolo and vibrato are chip-wide switches with no
            # IT equivalent; the samples already carry whatever the patch
            # sounds like without them.
            note("OPLDepth (chip-wide LFO switch)")
        elif isinstance(event, E.PSGToneOn):
            cell = grid.cell(row, CHANNEL_MAP[("psg", event.channel)])
            cell.note = _note_byte(event.note, event.octave)
            cell.instrument = sample_index.get(("psg", None), 0)
            cell.volume = _psg_volume(event.volume)
        elif isinstance(event, E.PSGToneOff):
            grid.cell(row, CHANNEL_MAP[("psg", event.channel)]).note = IT_NOTE_OFF
        elif isinstance(event, E.PSGNoiseOn):
            cell = grid.cell(row, CHANNEL_MAP[("noise", 0)])
            # Noise has no pitch; C-5 plays the sample at its native rate.
            cell.note = _note_byte(*REFERENCE_NOTE)
            cell.instrument = sample_index.get(
                ("noise", (bool(event.white), event.rate)), 0)
            cell.volume = _psg_volume(event.volume)
        elif isinstance(event, E.PSGNoiseOff):
            grid.cell(row, CHANNEL_MAP[("noise", 0)]).note = IT_NOTE_OFF
        elif isinstance(event, E.DACSample):
            cell = grid.cell(row, CHANNEL_MAP[("dac", 0)])
            cell.note = _note_byte(*REFERENCE_NOTE)
            cell.instrument = sample_index.get(("dac", event.name), 0)
            cell.volume = max(0, min(64, int(round(event.volume * 64))))
        elif isinstance(event, E.FMVolume):
            # Mxx sets channel volume, which is what FMVolume means.
            cell = grid.cell(row, CHANNEL_MAP[("fm", event.channel)])
            cell.effect = ord("M") - ord("A") + 1
            cell.param = max(0, min(64, int(round(event.volume * 64.0 / 127.0))))
        elif isinstance(event, E.FMPan):
            cell = grid.cell(row, CHANNEL_MAP[("fm", event.channel)])
            if event.left and event.right:
                pan = 32
            elif event.left:
                pan = 0
            elif event.right:
                pan = 64
            else:
                pan = 32
            if cell.volume < 0:
                cell.volume = 128 + pan      # volume column panning
            else:
                cell.effect = ord("X") - ord("A") + 1   # Xxx = set panning
                cell.param = min(255, pan * 4)
        elif isinstance(event, E.Tempo):
            cell = grid.cell(row, 0)
            speed, tempo, _ = _it_timing(1.0 / event.ticks_per_second
                                         * ticks_per_row)
            cell.effect = ord("T") - ord("A") + 1
            cell.param = tempo
        elif isinstance(event, E.FMPitch):
            pass                    # handled wholesale by _arpeggios, below
        elif isinstance(event, (E.Marker, E.LoopPoint, E.DACEnable)):
            pass                              # no IT equivalent worth faking
        else:
            note(type(event).__name__)

    for (row, channel), (x, y, exact) in _arpeggios(events, ticks_per_row).items():
        cell = grid.cell(row, CHANNEL_MAP[("fm", channel)])
        if cell.effect and cell.effect != _FX_ARPEGGIO:
            note("effect displaced by an arpeggio")
        cell.effect = _FX_ARPEGGIO
        cell.param = (x << 4) | y
        if not exact:
            note("arpeggio with more than three steps, truncated")

    return grid, skipped


#: IT numbers effects A=1..Z=26. J is arpeggio: Jxy cycles the note through
#: base, base+x, base+y on successive TICKS — which is what chipgen's `arp`
#: does with FMPitch, so the two line up exactly when the row has at least
#: three ticks in it. See PREFERRED_SPEED.
_FX_ARPEGGIO = ord("J") - ord("A") + 1


def _arpeggios(events, ticks_per_row: int):
    """Recover `arp` directives from the FMPitch events they expanded into.

    chipgen spells an arpeggio as a run of FMPitch events inside one row
    (see tracker.emit_arpeggio_row). IT spells the same thing as one
    effect. Reconstructing the offsets is better than dropping them: an
    arpeggio is often the only thing moving in a pattern, and a .it
    missing them would be missing the part a listener notices.

    Returns {(row, fm_channel): (x, y, exact)}, semitone offsets already
    clamped to the nibbles IT gives them.
    """
    E = events_mod
    per_row = {}
    tick = 0
    for event in events:
        if isinstance(event, E.Wait):
            tick += event.ticks
            continue
        if isinstance(event, E.End):
            break
        if isinstance(event, E.FMPitch):
            per_row.setdefault((tick // ticks_per_row, event.channel),
                               []).append(event.cents)

    out = {}
    for key, cents in per_row.items():
        semitones = []
        for value in cents:
            step = value / 100.0
            if abs(step - round(step)) > 0.01:
                semitones = []          # a real detune, not an arpeggio
                break
            semitones.append(int(round(step)))
        # An arpeggio starts on the note itself; anything else is a pitch
        # offset that IT's J cannot express.
        if len(semitones) < 2 or semitones[0] != 0:
            continue
        offsets = [s for s in semitones[1:] if 0 < s <= 15]
        if not offsets:
            continue
        exact = len(semitones) <= 3 and len(offsets) == len(semitones) - 1
        x = offsets[0]
        y = offsets[1] if len(offsets) > 1 else 0
        out[key] = (x, y, exact)
    return out


# --------------------------------------------------------------------------
# Deciding what to render
# --------------------------------------------------------------------------
def collect_samples(events, progress=None):
    """Render exactly the voices the score actually uses.

    Returns (samples, index). `samples` is the ordered list that goes into
    the file; `index` maps a voice key to its one-based IT sample number.
    Rendering a whole bank when a track uses five patches would cost more
    than the rest of the export put together, so this is driven by the
    event list, not by the bank.
    """
    E = events_mod
    wanted = []          # keys, in first-use order
    seen = set()

    def want(key):
        if key not in seen:
            seen.add(key)
            wanted.append(key)

    patch_of = {}
    opl_patch = {}
    for event in events:
        if isinstance(event, E.FMInstrumentSelect):
            patch_of[event.channel] = event.instrument
        elif isinstance(event, E.FMNoteOn):
            name = patch_of.get(event.channel)
            if name:
                want(("fm", name))
        elif isinstance(event, E.OPLInstrumentSelect):
            opl_patch[event.channel] = event.instrument
        elif isinstance(event, E.OPLNoteOn):
            name = opl_patch.get(event.channel)
            if name:
                want(("opl", name))
        elif isinstance(event, E.PSGToneOn):
            want(("psg", None))
        elif isinstance(event, E.PSGNoiseOn):
            want(("noise", (bool(event.white), event.rate)))
        elif isinstance(event, E.DACSample):
            if event.name in samples_mod.KIT:
                want(("dac", event.name))

    samples, index = [], {}
    for number, key in enumerate(wanted, start=1):
        kind, detail = key
        if progress:
            progress(number, len(wanted), key)
        if kind == "fm":
            data = render_fm_sample(instruments_mod.get(detail))
            name = detail
        elif kind == "opl":
            import opl_instruments
            data = render_opl_sample(opl_instruments.get(detail))
            name = f"OPL {detail}"[:25]
        elif kind == "psg":
            data = render_psg_tone_sample()
            name = "PSG square"
        elif kind == "noise":
            white, rate = detail
            data = render_psg_noise_sample(white=white, rate=rate)
            name = f"PSG {'white' if white else 'periodic'} noise {rate}"
        else:
            data = dac_sample(detail)
            name = f"DAC {detail}"
        data["name"] = name
        samples.append(data)
        index[key] = number
    return samples, index


# --------------------------------------------------------------------------
# Writing the file
# --------------------------------------------------------------------------
def _pack_pattern(rows, channels: int) -> bytes:
    """IT's packed pattern encoding.

    Each cell names the channel, then only the fields that changed. Two
    layers of redundancy removal: a per-channel "same mask as last time"
    shortcut, and per-field "same value as last time" bits. A pattern of
    mostly-empty rows costs almost nothing.
    """
    data = bytearray()
    last = [(0, 0, -1, 0, 0) for _ in range(channels)]   # note, ins, vol, fx, par
    seen = [0] * channels                                 # which fields are primed
    last_mask = [0xFF] * channels

    for row in rows:
        for channel in range(channels):
            cell = row[channel]
            if cell.empty():
                continue
            mask = 0
            note = cell.note
            if note:
                mask |= 1
                if note < 0x80:
                    note -= 1            # the packer's one-based convention
            if cell.instrument:
                mask |= 2
            if cell.volume >= 0:
                mask |= 4
            if cell.effect or cell.param:
                mask |= 8

            previous = last[channel]
            if mask & 1 and previous[0] == note and seen[channel] & 1:
                mask = (mask & ~1) | 0x10
            elif mask & 1:
                previous = (note,) + previous[1:]
                seen[channel] |= 1
            if mask & 2 and previous[1] == cell.instrument and seen[channel] & 2:
                mask = (mask & ~2) | 0x20
            elif mask & 2:
                previous = previous[:1] + (cell.instrument,) + previous[2:]
                seen[channel] |= 2
            if mask & 4 and previous[2] == cell.volume and seen[channel] & 4:
                mask = (mask & ~4) | 0x40
            elif mask & 4:
                previous = previous[:2] + (cell.volume,) + previous[3:]
                seen[channel] |= 4
            if (mask & 8 and previous[3] == cell.effect
                    and previous[4] == cell.param and seen[channel] & 8):
                mask = (mask & ~8) | 0x80
            elif mask & 8:
                previous = previous[:3] + (cell.effect, cell.param)
                seen[channel] |= 8
            last[channel] = previous

            if mask == last_mask[channel]:
                data.append(channel + 1)
            else:
                last_mask[channel] = mask
                data.append((channel + 1) | 0x80)
                data.append(mask)

            if mask & 1:
                data.append(note)
            if mask & 2:
                data.append(cell.instrument)
            if mask & 4:
                data.append(cell.volume)
            if mask & 8:
                data.append(cell.effect)
                data.append(cell.param)
        data.append(0)                    # end of row

    return struct.pack("<HHI", len(data), len(rows), 0) + bytes(data)


def _sample_header(sample, offset: int) -> bytes:
    loop = sample.get("loop")
    flags = 1 | 2                          # data present, 16-bit
    if loop:
        flags |= 16
    name = sample.get("name", "")[:25].encode("ascii", "replace")
    return struct.pack(
        "<4s12sBBBB26sBBIIIIIIIBBBB",
        b"IMPS",
        name[:12].ljust(12, b"\0"),
        0,                                 # always zero
        64,                                # global volume
        flags,
        64,                                # default volume
        name.ljust(26, b"\0"),
        1,                                 # cvt: signed samples
        32,                                # default pan (unused without 0x80)
        sample["frames"],
        loop[0] if loop else 0,
        loop[1] if loop else 0,
        int(sample["c5speed"]),
        0, 0,                              # no sustain loop
        offset,
        0, 0, 0, 0)                        # no vibrato


def build(events, meta=None, title: str = "", message: str = "",
          progress=None):
    """Assemble the whole .it in memory. Returns (bytes, report)."""
    import tracker as tracker_mod

    meta = meta or tracker_mod.Metadata()
    ticks_per_row = meta.ticks_per_row()
    row_seconds = ticks_per_row / float(meta.ticks_per_second)
    speed, tempo, timing_error = _it_timing(row_seconds)

    samples, index = collect_samples(events, progress=progress)
    if not samples:
        raise ITExportError("nothing to export: the event list plays no notes")
    grid, skipped = build_grid(events, ticks_per_row, index)

    # Pad to whole patterns; an IT order plays a fixed number of rows.
    rows = grid.rows or [[_Cell() for _ in range(IT_CHANNELS_USED)]]
    while len(rows) % IT_ROWS_PER_PATTERN:
        rows.append([_Cell() for _ in range(IT_CHANNELS_USED)])
    patterns = [rows[i:i + IT_ROWS_PER_PATTERN]
                for i in range(0, len(rows), IT_ROWS_PER_PATTERN)]

    packed = [_pack_pattern(p, IT_CHANNELS_USED) for p in patterns]
    orders = list(range(len(patterns))) + [255]

    channel_pan = _initial_panning(events)
    message_bytes = message.encode("ascii", "replace")[:8000]
    if message_bytes:
        message_bytes += b"\0"

    n_ord, n_ins, n_smp, n_pat = len(orders), 0, len(samples), len(patterns)
    # 0xC0 header + orders + parapointers + a zero edit-history count.
    base = 0xC0 + n_ord + 4 * (n_ins + n_smp + n_pat) + 2
    message_offset = base if message_bytes else 0
    cursor = base + len(message_bytes)

    smp_headers_at = cursor
    cursor += 80 * n_smp
    pattern_offsets = []
    for block in packed:
        pattern_offsets.append(cursor)
        cursor += len(block)
    sample_offsets = []
    for sample in samples:
        sample_offsets.append(cursor)
        cursor += len(sample["data"])

    flags = 1 | 8                       # stereo, linear slides (sample mode)
    special = 2 | (1 if message_bytes else 0)   # edit history present

    header = struct.pack(
        "<4s26sBBHHHHHHHHBBBBBBHII",
        b"IMPM",
        (title or meta.title or "chipgen").encode("ascii", "replace")[:25].ljust(26, b"\0"),
        4, 16,                          # row highlight: minor 4, major 16
        n_ord, n_ins, n_smp, n_pat,
        0x1000, 0x0214,                 # made with / compatible with IT 2.14
        flags, special,
        128,                            # global volume
        48,                             # mixing volume
        speed, tempo,
        128,                            # pan separation
        0,                              # pitch wheel depth
        len(message_bytes), message_offset, 0)

    out = bytearray(header)
    out += bytes(channel_pan)                     # 64 channels
    out += bytes([64] * 64)                       # channel volumes
    # 0xC0 is the fixed 64-byte header plus those two 64-byte arrays; the
    # order list starts here and every parapointer is measured from it.
    assert len(out) == 0xC0, len(out)
    out += bytes(orders)
    out += b"".join(struct.pack("<I", o) for o in [])          # no instruments
    out += b"".join(struct.pack("<I", smp_headers_at + 80 * i)
                    for i in range(n_smp))
    out += b"".join(struct.pack("<I", o) for o in pattern_offsets)
    out += struct.pack("<H", 0)                   # edit history: no entries
    out += message_bytes
    for sample, offset in zip(samples, sample_offsets):
        out += _sample_header(sample, offset)
    for block in packed:
        out += block
    for sample in samples:
        out += sample["data"]

    report = {
        "samples": [(s["name"], s["frames"], s["c5speed"], bool(s["loop"]))
                    for s in samples],
        "patterns": n_pat,
        "rows": len(rows),
        "speed": speed,
        "tempo": tempo,
        "timing_error_ms": timing_error * 1000.0,
        "skipped": skipped,
        "bytes": len(out),
    }
    return bytes(out), report


def _initial_panning(events):
    """Channel panning for the header, from the first FMPan on each channel."""
    pan = [32] * 64                     # 0 left, 32 centre, 64 right
    seen = set()
    for event in events:
        if not isinstance(event, events_mod.FMPan):
            continue
        channel = CHANNEL_MAP[("fm", event.channel)]
        if channel in seen:
            continue
        seen.add(channel)
        if event.left and event.right:
            pan[channel] = 32
        elif event.left:
            pan[channel] = 0
        elif event.right:
            pan[channel] = 64
    return pan


def export(events, path: str, meta=None, title: str = "", message: str = "",
           progress=None):
    data, report = build(events, meta=meta, title=title, message=message,
                         progress=progress)
    with open(path, "wb") as handle:
        handle.write(data)
    return report
