"""
bank_calibration.py — GENERATED. Do not edit by hand.

Per-patch loudness trims in Total Level steps (0.75 dB each), added
to a patch's carriers when it is selected. Positive attenuates,
negative boosts by removing attenuation.

Regenerate with:  python3 python/calibrate_bank.py --write
"""

TRIMS = {
    'bass': 5,
    'bell_pluck': 0,
    'brass': -4,
    'deep_bass': 5,
    'distorted_lead': 8,
    'e_piano': -4,
    'fm_stab': -6,
    'hard_pluck': 2,
    'jazz_chord_pad': -1,
    'metal_stab': -5,
    'orch_hit': -4,
    'organ': -1,
    'pluck_guitar': 0,
    'saw_lead': 4,
    'slap_bass': 7,
    'square_lead': -8,
    'strings': -1,
    'sub_bass': 6,
    'techno_bass': 1,
}
