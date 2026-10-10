"""
opl_instruments.py — the OPL2 patch bank.

Same job as instruments.py does for the YM2612, and the same contract: a
score names a patch, this resolves it. The built-in set is deliberately
small and generic — the interesting OPL sounds live in the thirty years of
`.sbi` and Furnace `.fui` files that opl_import.py reads, and shipping a
few hundred of those here would be someone else's work presented as ours.

What IS here is enough to write a track without importing anything: a
bass, two leads, a pad, an organ, a bell and a couple of percussion hits,
each built from the two operators the chip actually has.
"""

import json

from opl2 import OPLInstrument, OPLOperator

__all__ = ["BANK", "get", "names", "add", "load_bank", "describe"]


def _op(**kwargs) -> OPLOperator:
    return OPLOperator(**kwargs)


def patch(name, modulator, carrier, feedback=0, connection=0) -> OPLInstrument:
    return OPLInstrument(modulator=modulator, carrier=carrier,
                         feedback=feedback, connection=connection, name=name)


#: A modulator's total_level is the FM index — how hard it bends the
#: carrier — so it reads as "brightness", not "volume". A carrier's
#: total_level is the actual output level. That asymmetry is the whole
#: mental model for two-operator FM.
#: Carrier levels here were set by measuring, not by ear: the bank started
#: 21 dB apart end to end, which means swapping one lead for another
#: silently remixes the arrangement. The two drums are deliberately left
#: alone — they are transients, and a 300 ms measurement window judges
#: them against the silence after the hit rather than the hit.
BANK = {}


def _register(instrument):
    BANK[instrument.name] = instrument
    return instrument


_register(patch(
    "opl_bass",
    # Modulator an octave down and fairly hot: the classic OPL bass growl
    # is a sub-harmonic modulator, not a filter.
    _op(multiple=0, total_level=18, attack=15, decay=6, sustain_level=3,
        release=6, sustaining=True),
    _op(multiple=1, total_level=3, attack=15, decay=7, sustain_level=2,
        release=6, sustaining=True),
    feedback=6))

_register(patch(
    "opl_square_lead",
    _op(multiple=1, total_level=26, attack=15, decay=0, sustain_level=0,
        release=7, sustaining=True),
    # Half-sine on the carrier is what makes an OPL lead cut: it doubles
    # the harmonic content of a plain sine for nothing.
    _op(multiple=1, total_level=0, attack=15, decay=0, sustain_level=0,
        release=7, sustaining=True, waveform=1),
    feedback=7))

_register(patch(
    "opl_saw_lead",
    _op(multiple=1, total_level=14, attack=15, decay=2, sustain_level=1,
        release=7, sustaining=True, waveform=2),
    _op(multiple=1, total_level=6, attack=15, decay=0, sustain_level=0,
        release=7, sustaining=True, waveform=3),
    feedback=5))

_register(patch(
    "opl_pad",
    _op(multiple=1, total_level=32, attack=6, decay=4, sustain_level=2,
        release=4, sustaining=True, key_scale_level=1),
    _op(multiple=1, total_level=8, attack=5, decay=3, sustain_level=1,
        release=3, sustaining=True),
    feedback=2))

_register(patch(
    "opl_organ",
    # Additive rather than FM: two sines a fifth apart is a drawbar organ,
    # and it is the one thing two operators do better side by side.
    _op(multiple=1, total_level=11, attack=15, decay=0, sustain_level=0,
        release=8, sustaining=True),
    _op(multiple=3, total_level=15, attack=15, decay=0, sustain_level=0,
        release=8, sustaining=True),
    feedback=0, connection=1))

_register(patch(
    "opl_bell",
    # An inharmonic modulator ratio is what makes metal sound like metal.
    _op(multiple=7, total_level=22, attack=15, decay=6, sustain_level=6,
        release=8, sustaining=False),
    _op(multiple=1, total_level=0, attack=15, decay=8, sustain_level=4,
        release=8, sustaining=False),
    feedback=0))

_register(patch(
    "opl_kick",
    _op(multiple=1, total_level=8, attack=15, decay=12, sustain_level=15,
        release=13, sustaining=False),
    _op(multiple=0, total_level=0, attack=15, decay=11, sustain_level=15,
        release=12, sustaining=False),
    feedback=7))

_register(patch(
    "opl_snare",
    _op(multiple=12, total_level=4, attack=15, decay=11, sustain_level=15,
        release=12, sustaining=False, waveform=3),
    _op(multiple=15, total_level=0, attack=15, decay=10, sustain_level=15,
        release=11, sustaining=False, waveform=3),
    feedback=7))


def names():
    return sorted(BANK)


def get(name: str) -> OPLInstrument:
    """Resolve a patch name, or say which ones exist.

    A bare KeyError from inside the renderer tells a model nothing it can
    act on; the list of names tells it exactly what to write instead.
    """
    try:
        return BANK[name]
    except KeyError:
        raise KeyError(
            f"no OPL2 instrument named {name!r}. Available: "
            f"{', '.join(names())}. Import more with "
            f"python/opl_import.py and pass --opl-bank.") from None


def add(instrument) -> None:
    BANK[instrument.name] = instrument


def load_bank(path: str) -> int:
    """Merge a bank written by opl_import.save_bank. Returns how many."""
    import opl_import
    loaded = opl_import.load_bank(path)
    BANK.update(loaded)
    return len(loaded)


def describe() -> dict:
    out = {}
    for name, instrument in BANK.items():
        out[name] = {
            "feedback": instrument.feedback,
            "connection": "additive" if instrument.connection else "fm",
            "modulator_level": instrument.modulator.total_level,
            "carrier_level": instrument.carrier.total_level,
            "waveforms": [instrument.modulator.waveform,
                          instrument.carrier.waveform],
        }
    return out
