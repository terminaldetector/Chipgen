"""common.py — the envelope inverse, shared by every chip.

descriptors.py defines the time axes by what a render does:

    body   the level 0.6 s after the note starts, against its peak
    decay  how long the fall from the peak toward that level takes to get
           63% of the way there

and every chip has envelope generators whose own rates are in different
units. This is the one place that turns "what the DNA says the envelope
should *measure*" into plain numbers (how fast to fall, where to stop),
which each backend then converts to its own rate table. Doing the inverse
against the definition, rather than against what a rate "usually" means,
is what keeps the compilers honest across chips.
"""

import math

#: seconds; must equal descriptors.BODY_AT
T_REF = 0.6


def attack_seconds(axis: float) -> float:
    """The attack axis as a rise time (descriptors.ATTACK_*)."""
    return 0.002 * (math.exp(axis * math.log(201.0)) - 1.0)


def decay_seconds(axis: float) -> float:
    return 0.03 * math.exp(axis * math.log(100.0))


def release_seconds(axis: float) -> float:
    return 0.02 * math.exp(axis * math.log(50.0))


def level_of(body: float) -> float:
    """The body axis as an amplitude ratio against the peak."""
    return 10.0 ** (-40.0 * (1.0 - body) / 20.0)


def envelope_plan(body: float, decay: float) -> dict:
    """-> {"hold": bool, "level": L, "slope": dB/s, "floor": amplitude}.

    `level` is where the note is at T_REF. A note whose level there is
    near 1 does not fall at all (`hold`). Otherwise it must fall to that
    level by T_REF *and* take the time the decay axis asks to get 63% of
    the way: if the two disagree (a long decay that has to be 30 dB down
    in 0.6 s is a contradiction) the body wins, because it is the level a
    listener hears and the decay is only how it got there.
    """
    level = level_of(body)
    if level >= 0.97:
        return {"hold": True, "level": 1.0, "slope": 0.0, "floor": 1.0}
    target = level + (1.0 - level) / math.e
    t_d = decay_seconds(decay)
    slope_decay = -20.0 * math.log10(target) / t_d
    slope_body = -20.0 * math.log10(level) / (T_REF * 0.9)
    slope = max(slope_decay, slope_body)
    return {"hold": False, "level": level, "slope": slope,
            "floor": level * 0.85}


def db_steps(amplitude: float, step_db: float, limit: int) -> int:
    """An amplitude ratio as a count of attenuation steps."""
    if amplitude <= 1e-6:
        return limit
    return int(round(min(limit, max(0.0, -20.0 * math.log10(amplitude)
                                    / step_db))))


# -- detune ------------------------------------------------------------------
#: descriptors reads detune as the depth of slow amplitude beating, so the
#: compilers get it by the one thing that sets that depth: how loud the
#: second voice is against the first (two sines of amplitude 1 and a beat
#: with a depth of about a). The pitch offset only sets how fast it beats.
BEAT_HZ = 1.2          # slow enough to stay under descriptors' 3.5 Hz line


def detune_ratio(detune: float) -> float:
    """Amplitude of the second voice against the first for a detune axis."""
    from .. import descriptors
    return descriptors.MOVEMENT_FLOOR + descriptors.DETUNE_FULL * detune


def detune_cents(register, beat_hz: float = BEAT_HZ) -> float:
    """How far sharp the second voice goes so that the two beat at about
    `beat_hz` at the register the instrument is aimed at."""
    import opn2
    hz = opn2.note_to_freq(*register)
    cents = 1200.0 * math.log2(1.0 + beat_hz / hz)
    return max(2.0, min(30.0, cents))
