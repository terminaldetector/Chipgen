"""
bank_calibration.py — GENERATED. Do not edit by hand.

Per-patch loudness trims in Total Level steps (0.75 dB each), added
to a patch's carriers when it is selected. Positive attenuates,
negative boosts by removing attenuation.

Regenerate with:  python3 python/calibrate_bank.py --write
"""

TRIMS = {
    'bass': 5,
    'bell_pluck': 1,
    'brass': -3,
    'distorted_lead': 8,
    'e_piano': -4,
    'jazz_chord_pad': 0,
    'metal_stab': -4,
    'orch_hit': -3,
    'organ': 0,
    'pluck_guitar': 1,
    'slap_bass': 6,
    'square_lead': -8,
    'strings': 0,
    'sub_bass': 7,
}
