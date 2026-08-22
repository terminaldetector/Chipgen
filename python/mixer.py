"""
mixer.py — the one place the two chips get summed.

Both the sequencer (rendering an event list) and the VGM player (replaying
a register log) end up holding the same two things: FM audio at ~53 kHz
stereo and PSG audio at ~223 kHz mono. Both then have to resample, balance
and normalise them. Having that written twice is how the two drift, and
they did: the moment the sequencer's PSG gain and DC blocking changed, a
VGM replayed through the player came out 3.5 dB away from the render it
was exported from — same music, same registers, different answer.

So it lives here once, and both call it.
"""

import audio as _audio

#: Relative level of the PSG against the FM chip.
#:
#: The two cores were scaled independently and never reconciled. The FM
#: path is scaled so six channels at maximum fill int16, which puts ONE
#: channel at 0.164 of full scale. psg.c divides its four-channel mix by
#: four and scales to full int16, putting one PSG voice at 0.25 — nearly
#: 4 dB above a full FM channel. The result was a mix where a single
#: square and a hi-hat matched five FM voices in RMS and buried them.
#:
#: 0.65 puts one PSG voice level with one FM channel at maximum. That is
#: an engineering choice, not a measurement off real hardware: the Mega
#: Drive mixes the two chips in an analog stage whose ratio varies by
#: board revision. Override it if you are matching a specific console.
DEFAULT_PSG_GAIN = 0.65

#: The OPL2 is mono and its nine voices already share one output stage, so
#: it arrives hotter than the PSG per note. Set by the same reasoning as
#: the PSG gain: loud enough to sit in the mix, quiet enough that nine
#: voices at once do not swamp the FM.
DEFAULT_OPL_GAIN = 0.75

#: Ceiling for the safety limiter, leaving a little room below clipping.
#: This only ever attenuates, and only when a mix would otherwise clip.
NORMALISE_TARGET = 0.98

#: Peak level the CLI masters to. A Genesis playing three voices does not
#: reach full scale and neither does this — an honest render of a sparse
#: arrangement lands near -7 dBFS, which is correct and also quieter than
#: anything else the listener will play next. So the library leaves levels
#: alone (renders stay comparable to each other, which is what you want
#: when you are comparing takes) and the CLI masters to here, because a
#: file you hand someone should be a file they can just play.
DEFAULT_MASTER_PEAK = 0.89


def mix(fm_audio, psg_audio, fm_rate: float, psg_rate: float,
        target_rate: int, fm_gain: float = 1.0,
        psg_gain: float = DEFAULT_PSG_GAIN, dc_block: bool = True,
        opl_audio=None, opl_rate: float = 0.0,
        opl_gain: float = DEFAULT_OPL_GAIN):
    """Resample every chip to target_rate, balance, centre and normalise.

    The OPL2 is optional and mono, and is skipped entirely when a score
    does not use it — resampling an empty buffer is cheap, but building
    one is not.
    """
    fm = _audio.resample(fm_audio, fm_rate, target_rate)
    psg = _audio.resample(psg_audio, psg_rate, target_rate)
    opl = (_audio.resample(opl_audio, opl_rate, target_rate)
           if opl_audio is not None and len(opl_audio) and opl_rate else None)

    frames = max(len(fm), len(psg), len(opl) if opl is not None else 0)
    out = _audio.zeros(frames, 2)
    if len(fm):
        _audio.add_stereo_into(out, fm, fm_gain)
    if len(psg):
        _audio.add_mono_into_stereo(out, psg, psg_gain)
    if opl is not None and len(opl):
        _audio.add_mono_into_stereo(out, opl, opl_gain)

    if dc_block:
        out = remove_dc(out)

    peak = _audio.peak(out)
    if peak > 1.0:
        out = _audio.scale(out, NORMALISE_TARGET / peak)
    return out


def normalize_peak(buf, target: float = DEFAULT_MASTER_PEAK):
    """Scale so the loudest sample sits at `target`. Mastering, not emulation.

    Deliberately peak, not loudness: a chiptune's dynamics are the point,
    and pulling a quiet passage up to match a loud one would be inventing
    a compressor the hardware never had.
    """
    peak = _audio.peak(buf)
    if peak <= 0 or target <= 0:
        return buf
    return _audio.scale(buf, target / peak)


def remove_dc(buf):
    """Subtract each channel's DC term.

    A one-pole high-pass would be the textbook answer, but it is recursive
    and so cannot be vectorised over time, and here it would be doing the
    same job the long way round: the offset is static — the YM2612's DAC
    ladder bias, measured at -0.027 or 4.3% of peak on the demo — not
    drifting. Subtracting the mean removes exactly that and leaves the
    audio band alone, including the ladder's actual grit, which is a
    signal-dependent square rather than a constant.
    """
    if not len(buf):
        return buf
    if _audio.HAVE_NUMPY and not _audio.is_fallback(buf):
        import numpy as np
        centre = buf.mean(axis=0, keepdims=True) if buf.ndim == 2 else buf.mean()
        return (buf - centre).astype(np.float32)

    data = buf.data
    channels = buf.channels
    for ch in range(channels):
        column = range(ch, len(data), channels)
        total = 0.0
        count = 0
        for i in column:
            total += data[i]
            count += 1
        mean = total / count if count else 0.0
        for i in column:
            data[i] -= mean
    return buf
