"""
instruments.py — the YM2612 FM patch bank.

A generative model selects instruments BY NAME/ID rather than programming
raw operator physics per note — same division of labour as a DefleMask
instrument bank referenced from pattern data. Adding a voice here makes it
immediately available to every backend, the tracker notation, the LLM
prompt and bridge/manifest.json, because they all read this one dict.

## The operator-order trap

The YM2612's register offsets ascend as op1, op3, op2, op4 — the chip
interleaves operators across its 24-slot pipeline, and the register map
follows the pipeline, not the block diagram. FMInstrument.operators is
stored in that register order. Every algorithm diagram you will find
online, and every other tracker, numbers operators op1..op4.

So: build patches with `patch(...)`, which takes op1..op4 in the ordinary
numbering and does the shuffle for you. The four original voices below are
written in raw register order for a reason — see the note above BASS.

## Algorithms (which operator modulates which; carriers reach the output)

    0: 1>2>3>4            4: 1>2, 3>4          (2 carriers)
    1: (1,2)>3>4          5: 1>2, 1>3, 1>4     (3 carriers)
    2: 1>4, 2>3>4         6: 1>2, 3, 4         (3 carriers)
    3: 1>2>4, 3>4         7: 1, 2, 3, 4        (4 carriers, additive)
"""

import json

from opn2 import Operator, FMInstrument

__all__ = ["BANK", "patch", "names", "describe", "get", "add",
           "instrument_to_dict", "instrument_from_dict", "load_bank",
           "save_bank"]


def patch(name: str, algorithm: int, feedback: int, op1: Operator,
          op2: Operator, op3: Operator, op4: Operator) -> FMInstrument:
    """Build a patch from operators in the usual op1..op4 numbering.

    The reordering to the chip's register order (op1, op3, op2, op4) happens
    here, once, instead of in your head every time.
    """
    return FMInstrument(algorithm, feedback, [op1, op3, op2, op4], name)


# --------------------------------------------------------------------------
# The original four, in the spirit of the Contra: Hard Corps / Genesis
# "techno-metal" sound this project started from.
#
# These are written as raw register-order lists rather than through patch()
# on purpose: they were tuned by ear against exactly these register writes,
# and the demo WAV checked into the repo is a rendering of them. Rewriting
# them through patch() would silently move two operators and change how the
# shipped track sounds.
# --------------------------------------------------------------------------
BASS = FMInstrument(
    algorithm=0, feedback=5, name="bass",
    operators=[
        Operator(detune=0, multiple=1, total_level=28, attack_rate=31,
                  decay_rate=8, sustain_rate=2, release_rate=8, sustain_level=3),
        Operator(detune=0, multiple=2, total_level=30, attack_rate=31,
                  decay_rate=10, sustain_rate=2, release_rate=8, sustain_level=4),
        Operator(detune=0, multiple=1, total_level=22, attack_rate=31,
                  decay_rate=6, sustain_rate=2, release_rate=8, sustain_level=3),
        Operator(detune=0, multiple=1, total_level=6, attack_rate=31,
                  decay_rate=5, sustain_rate=2, release_rate=9, sustain_level=2),
    ],
)

DISTORTED_LEAD = FMInstrument(
    algorithm=0, feedback=7, name="distorted_lead",  # heavy self-feedback on op1 -> gritty/"guitar" edge
    operators=[
        Operator(detune=3, multiple=1, total_level=32, attack_rate=31,
                  decay_rate=4, sustain_rate=1, release_rate=7, sustain_level=4),
        Operator(detune=1, multiple=1, total_level=30, attack_rate=31,
                  decay_rate=6, sustain_rate=1, release_rate=7, sustain_level=4),
        Operator(detune=2, multiple=1, total_level=26, attack_rate=31,
                  decay_rate=5, sustain_rate=1, release_rate=7, sustain_level=3),
        Operator(detune=0, multiple=1, total_level=2, attack_rate=31,
                  decay_rate=3, sustain_rate=2, release_rate=6, sustain_level=1),
    ],
)

BELL_PLUCK = FMInstrument(
    algorithm=4, feedback=2, name="bell_pluck",  # two independent 2-op pairs -> clean partials
    operators=[
        Operator(detune=0, multiple=1, total_level=4, attack_rate=31,
                  decay_rate=14, sustain_rate=8, release_rate=10, sustain_level=8),
        Operator(detune=0, multiple=7, total_level=20, attack_rate=31,
                  decay_rate=18, sustain_rate=10, release_rate=12, sustain_level=10),
        Operator(detune=0, multiple=1, total_level=6, attack_rate=31,
                  decay_rate=12, sustain_rate=6, release_rate=10, sustain_level=7),
        Operator(detune=0, multiple=3, total_level=16, attack_rate=31,
                  decay_rate=16, sustain_rate=8, release_rate=11, sustain_level=9),
    ],
)

JAZZ_CHORD_PAD = FMInstrument(
    algorithm=6, feedback=1, name="jazz_chord_pad",  # multiple carriers -> softer, more "additive" chord tone
    operators=[
        Operator(detune=0, multiple=1, total_level=14, attack_rate=18,
                  decay_rate=4, sustain_rate=1, release_rate=6, sustain_level=3),
        Operator(detune=1, multiple=2, total_level=18, attack_rate=16,
                  decay_rate=4, sustain_rate=1, release_rate=6, sustain_level=3),
        Operator(detune=6, multiple=1, total_level=16, attack_rate=17,
                  decay_rate=4, sustain_rate=1, release_rate=6, sustain_level=3),
        Operator(detune=2, multiple=3, total_level=20, attack_rate=15,
                  decay_rate=5, sustain_rate=1, release_rate=6, sustain_level=4),
    ],
)

# --------------------------------------------------------------------------
# Added voices. Written through patch(), i.e. op1..op4 in normal numbering.
# --------------------------------------------------------------------------
SUB_BASS = patch(
    "sub_bass", algorithm=7, feedback=0,
    # Four independent carriers, three of them muted: what is left is one
    # sine. Nothing is more "sub" than a sine, and FM cannot make a cleaner
    # one than by simply not modulating it.
    op1=Operator(multiple=1, total_level=127, attack_rate=31, decay_rate=0,
                 sustain_rate=0, release_rate=15, sustain_level=0),
    op2=Operator(multiple=1, total_level=127, attack_rate=31, decay_rate=0,
                 sustain_rate=0, release_rate=15, sustain_level=0),
    op3=Operator(multiple=1, total_level=127, attack_rate=31, decay_rate=0,
                 sustain_rate=0, release_rate=15, sustain_level=0),
    op4=Operator(multiple=1, total_level=4, attack_rate=31, decay_rate=6,
                 sustain_rate=0, release_rate=8, sustain_level=1),
)

SLAP_BASS = patch(
    # A slap is a bright transient over a clean body: the modulators have to
    # be LOUD for a few tens of milliseconds and then get out of the way.
    #
    # The first version of this patch did not slap at all. Its modulators sat
    # at Total Level 26 and 30 — 19 and 22 dB of attenuation — which is too
    # quiet to modulate a carrier meaningfully, so both carriers ran as
    # near-pure sines. Measured against sub_bass (an intentional pure sine)
    # it was identical to within a few tenths of a percent: 18% of energy
    # above 200 Hz during the attack against sub_bass's 7%, and 0.1% in
    # sustain against sub_bass's 0.1%. The name promised a character the
    # patch could not produce.
    #
    # Modulators now start at TL 12/18 and decay to silence (sustain level
    # 15/13) at rates 18/16 — that combination measures 95% of its energy
    # above 200 Hz during the attack, against 8% before, while still being
    # gone within about 100 ms so the body stays clean.
    #
    # Decay rate needed care in both directions: at rate 24 the modulator
    # was gone so fast that the transient measured WEAKER (24%) than at 18
    # (95%). Faster is not brighter — past a point the bright part finishes
    # before it has been heard.
    "slap_bass", algorithm=4, feedback=4,
    op1=Operator(detune=3, multiple=3, total_level=12, attack_rate=31,
                 decay_rate=18, sustain_rate=18, release_rate=12, sustain_level=15),
    op2=Operator(detune=0, multiple=1, total_level=8, attack_rate=31,
                 decay_rate=9, sustain_rate=3, release_rate=9, sustain_level=4),
    op3=Operator(detune=5, multiple=2, total_level=18, attack_rate=31,
                 decay_rate=16, sustain_rate=15, release_rate=11, sustain_level=13),
    op4=Operator(detune=0, multiple=1, total_level=12, attack_rate=31,
                 decay_rate=8, sustain_rate=2, release_rate=9, sustain_level=3),
)

DEEP_BASS = patch(
    # The middle ground the bank was missing: more body than sub_bass, less
    # bite than bass. One modulator at a moderate level with a slow decay,
    # so the harmonics fade over the length of a note instead of snapping
    # away — a bass that stays audible on small speakers without fighting a
    # lead for the 200-800 Hz range.
    "deep_bass", algorithm=4, feedback=2,
    op1=Operator(detune=0, multiple=1, total_level=20, attack_rate=31,
                 decay_rate=11, sustain_rate=4, release_rate=8, sustain_level=6),
    op2=Operator(detune=0, multiple=1, total_level=6, attack_rate=31,
                 decay_rate=7, sustain_rate=1, release_rate=8, sustain_level=2),
    op3=Operator(detune=3, multiple=2, total_level=26, attack_rate=31,
                 decay_rate=13, sustain_rate=6, release_rate=9, sustain_level=8),
    op4=Operator(detune=0, multiple=1, total_level=10, attack_rate=31,
                 decay_rate=6, sustain_rate=1, release_rate=8, sustain_level=2),
)

TECHNO_BASS = patch(
    # Driving and harmonically dense, for four-on-the-floor material where
    # the bass IS the hook. Algorithm 3 feeds op4 from two directions
    # (1>2>4 and 3>4), so the harmonics stack rather than sitting in one
    # series, and feedback 6 keeps it buzzing under the fundamental.
    "techno_bass", algorithm=3, feedback=6,
    op1=Operator(detune=1, multiple=1, total_level=18, attack_rate=31,
                 decay_rate=14, sustain_rate=6, release_rate=10, sustain_level=7),
    op2=Operator(detune=0, multiple=2, total_level=22, attack_rate=31,
                 decay_rate=12, sustain_rate=5, release_rate=10, sustain_level=6),
    op3=Operator(detune=5, multiple=1, total_level=24, attack_rate=31,
                 decay_rate=15, sustain_rate=7, release_rate=10, sustain_level=8),
    op4=Operator(detune=0, multiple=1, total_level=8, attack_rate=31,
                 decay_rate=8, sustain_rate=2, release_rate=9, sustain_level=3),
)

BRASS = patch(
    # The classic FM brass move: a modulator whose envelope opens SLOWER
    # than the carrier's, so the tone brightens after the attack the way a
    # real horn does when the player leans into it.
    "brass", algorithm=2, feedback=5,
    op1=Operator(detune=3, multiple=1, total_level=30, attack_rate=22,
                 decay_rate=8, sustain_rate=2, release_rate=8, sustain_level=2),
    op2=Operator(detune=0, multiple=1, total_level=34, attack_rate=20,
                 decay_rate=7, sustain_rate=2, release_rate=8, sustain_level=2),
    op3=Operator(detune=5, multiple=1, total_level=28, attack_rate=21,
                 decay_rate=6, sustain_rate=1, release_rate=8, sustain_level=2),
    op4=Operator(detune=0, multiple=1, total_level=12, attack_rate=25,
                 decay_rate=5, sustain_rate=1, release_rate=9, sustain_level=2),
)

E_PIANO = patch(
    # DX7 Rhodes lineage: one modulator feeding three carriers at
    # non-integer-sounding multiples, everything decaying, nothing
    # sustaining.
    "e_piano", algorithm=5, feedback=4,
    op1=Operator(detune=0, multiple=1, total_level=32, attack_rate=31,
                 decay_rate=14, sustain_rate=6, release_rate=10, sustain_level=6),
    op2=Operator(detune=1, multiple=1, total_level=12, attack_rate=31,
                 decay_rate=10, sustain_rate=4, release_rate=9, sustain_level=5),
    op3=Operator(detune=6, multiple=2, total_level=22, attack_rate=31,
                 decay_rate=13, sustain_rate=6, release_rate=10, sustain_level=7),
    op4=Operator(detune=2, multiple=4, total_level=30, attack_rate=31,
                 decay_rate=16, sustain_rate=8, release_rate=11, sustain_level=9),
)

ORGAN = patch(
    # Algorithm 7 is additive, so the four multiples are drawbars:
    # 1, 2, 4, 8 = fundamental plus three octaves.
    "organ", algorithm=7, feedback=0,
    op1=Operator(multiple=1, total_level=14, attack_rate=31, decay_rate=0,
                 sustain_rate=0, release_rate=12, sustain_level=0),
    op2=Operator(multiple=2, total_level=20, attack_rate=31, decay_rate=0,
                 sustain_rate=0, release_rate=12, sustain_level=0),
    op3=Operator(multiple=4, total_level=26, attack_rate=31, decay_rate=0,
                 sustain_rate=0, release_rate=12, sustain_level=0),
    op4=Operator(multiple=8, total_level=32, attack_rate=31, decay_rate=0,
                 sustain_rate=0, release_rate=12, sustain_level=0),
)

SQUARE_LEAD = patch(
    "square_lead", algorithm=4, feedback=7,
    op1=Operator(detune=0, multiple=1, total_level=24, attack_rate=31,
                 decay_rate=0, sustain_rate=0, release_rate=10, sustain_level=0),
    op2=Operator(detune=0, multiple=1, total_level=8, attack_rate=31,
                 decay_rate=0, sustain_rate=0, release_rate=10, sustain_level=0),
    op3=Operator(detune=4, multiple=3, total_level=34, attack_rate=31,
                 decay_rate=0, sustain_rate=0, release_rate=10, sustain_level=0),
    op4=Operator(detune=0, multiple=1, total_level=18, attack_rate=31,
                 decay_rate=0, sustain_rate=0, release_rate=10, sustain_level=0),
)

PLUCK_GUITAR = patch(
    "pluck_guitar", algorithm=0, feedback=6,
    op1=Operator(detune=1, multiple=2, total_level=30, attack_rate=31,
                 decay_rate=18, sustain_rate=14, release_rate=12, sustain_level=12),
    op2=Operator(detune=0, multiple=1, total_level=26, attack_rate=31,
                 decay_rate=14, sustain_rate=10, release_rate=11, sustain_level=9),
    op3=Operator(detune=2, multiple=1, total_level=22, attack_rate=31,
                 decay_rate=12, sustain_rate=8, release_rate=11, sustain_level=7),
    op4=Operator(detune=0, multiple=1, total_level=6, attack_rate=31,
                 decay_rate=10, sustain_rate=6, release_rate=10, sustain_level=5),
)

STRINGS = patch(
    # Slow attack on everything plus detune spread across the carriers —
    # the detune is the ensemble: several near-identical pitches beating
    # against each other is what a string section physically is.
    "strings", algorithm=4, feedback=2,
    op1=Operator(detune=1, multiple=1, total_level=30, attack_rate=12,
                 decay_rate=4, sustain_rate=0, release_rate=5, sustain_level=1),
    op2=Operator(detune=6, multiple=1, total_level=14, attack_rate=11,
                 decay_rate=3, sustain_rate=0, release_rate=5, sustain_level=1),
    op3=Operator(detune=2, multiple=2, total_level=34, attack_rate=13,
                 decay_rate=4, sustain_rate=0, release_rate=5, sustain_level=1),
    op4=Operator(detune=5, multiple=1, total_level=16, attack_rate=12,
                 decay_rate=3, sustain_rate=0, release_rate=5, sustain_level=1),
)

ORCH_HIT = patch(
    # Every operator at a different high multiple with a hard decay: a
    # short inharmonic blast. The 90s in one voice.
    #
    # Two changes from the first draft, both forced by calibration.
    # Carriers are 6 steps hotter, and the decay is 4 steps slower with a
    # deeper sustain level. As drafted this measured 12 dB under the bank
    # and the trim could only give back 7.5 before its quietest carrier hit
    # Total Level 0 — a trim lowers a patch freely but raises one only as
    # far as its own headroom, so a patch has to be designed slightly HOT
    # and trimmed down, never designed quiet and trimmed up. The decay was
    # the other half: a stab that has fully decayed inside 150 ms measures
    # quiet however loud its attack is, because there is nothing left to
    # measure.
    "orch_hit", algorithm=6, feedback=7,
    op1=Operator(detune=3, multiple=6, total_level=20, attack_rate=31,
                 decay_rate=18, sustain_rate=20, release_rate=14, sustain_level=10),
    op2=Operator(detune=1, multiple=3, total_level=4, attack_rate=31,
                 decay_rate=16, sustain_rate=18, release_rate=14, sustain_level=9),
    op3=Operator(detune=6, multiple=9, total_level=10, attack_rate=31,
                 decay_rate=17, sustain_rate=19, release_rate=14, sustain_level=10),
    op4=Operator(detune=2, multiple=13, total_level=16, attack_rate=31,
                 decay_rate=19, sustain_rate=20, release_rate=15, sustain_level=11),
)

METAL_STAB = patch(
    "metal_stab", algorithm=3, feedback=7,
    op1=Operator(detune=7, multiple=8, total_level=26, attack_rate=31,
                 decay_rate=24, sustain_rate=16, release_rate=13, sustain_level=13),
    op2=Operator(detune=3, multiple=5, total_level=30, attack_rate=31,
                 decay_rate=20, sustain_rate=12, release_rate=12, sustain_level=11),
    op3=Operator(detune=1, multiple=2, total_level=28, attack_rate=31,
                 decay_rate=18, sustain_rate=10, release_rate=12, sustain_level=9),
    op4=Operator(detune=0, multiple=1, total_level=8, attack_rate=31,
                 decay_rate=14, sustain_rate=8, release_rate=11, sustain_level=7),
)

SAW_LEAD = patch(
    # A sawtooth built additively rather than by modulation.
    #
    # Algorithm 7 is four independent carriers, so setting their multiples to
    # 1, 2, 3, 4 and their levels to 1/n gives the first four harmonics of a
    # sawtooth directly — no modulation index to drift, no feedback to go
    # noisy at the top of the range. The Total Level offsets below ARE that
    # 1/n series: a level step is 0.75 dB, and 20*log10(n)/0.75 rounds to
    # 0, 8, 13, 16 for n = 1, 2, 3, 4.
    #
    # Measured on A3: harmonics come out at 1.000, 0.471, 0.314, 0.225 of
    # the fundamental against an ideal saw's 1.000, 0.500, 0.333, 0.250 —
    # and nothing at all above the fourth, because four operators is four
    # harmonics. A band-limited saw, by construction rather than by luck.
    #
    # Worth having because every other bright voice in this bank gets its
    # brightness from feedback, which is unpredictable across the register.
    # This one is the same shape at every pitch.
    "saw_lead", algorithm=7, feedback=0,
    op1=Operator(detune=0, multiple=1, total_level=8, attack_rate=31,
                 decay_rate=6, sustain_rate=0, release_rate=8, sustain_level=2),
    op2=Operator(detune=1, multiple=2, total_level=16, attack_rate=31,
                 decay_rate=7, sustain_rate=0, release_rate=8, sustain_level=2),
    op3=Operator(detune=0, multiple=3, total_level=21, attack_rate=31,
                 decay_rate=7, sustain_rate=0, release_rate=8, sustain_level=2),
    op4=Operator(detune=2, multiple=4, total_level=24, attack_rate=31,
                 decay_rate=8, sustain_rate=0, release_rate=8, sustain_level=3),
)

HARD_PLUCK = patch(
    # Hard attack, immediate decay, and then a LONG tail: release_rate 3
    # against the bank's usual 8-12. On this chip release rate counts down,
    # so a small number is a slow release — the note keeps ringing after
    # note-off instead of stopping with it, which is what makes a pluck
    # sound plucked rather than gated.
    "hard_pluck", algorithm=4, feedback=5,
    op1=Operator(detune=2, multiple=2, total_level=14, attack_rate=31,
                 decay_rate=22, sustain_rate=14, release_rate=4, sustain_level=14),
    op2=Operator(detune=0, multiple=1, total_level=6, attack_rate=31,
                 decay_rate=13, sustain_rate=7, release_rate=3, sustain_level=7),
    op3=Operator(detune=6, multiple=3, total_level=20, attack_rate=31,
                 decay_rate=20, sustain_rate=12, release_rate=4, sustain_level=12),
    op4=Operator(detune=0, multiple=1, total_level=10, attack_rate=31,
                 decay_rate=15, sustain_rate=8, release_rate=3, sustain_level=8),
)

FM_STAB = patch(
    # The short percussive hit techno leans on: pitched enough to sit in a
    # key, transient enough to work on an off-beat. Everything decays fast
    # from a loud start, and the modulator's non-integer-feeling multiple
    # (5 against a carrier at 1) puts a metallic edge on the front without
    # making it a full orchestra hit.
    "fm_stab", algorithm=2, feedback=6,
    op1=Operator(detune=3, multiple=5, total_level=16, attack_rate=31,
                 decay_rate=22, sustain_rate=18, release_rate=13, sustain_level=14),
    op2=Operator(detune=0, multiple=2, total_level=20, attack_rate=31,
                 decay_rate=20, sustain_rate=16, release_rate=13, sustain_level=13),
    op3=Operator(detune=5, multiple=1, total_level=18, attack_rate=31,
                 decay_rate=19, sustain_rate=15, release_rate=12, sustain_level=12),
    op4=Operator(detune=0, multiple=1, total_level=6, attack_rate=31,
                 decay_rate=16, sustain_rate=12, release_rate=11, sustain_level=10),
)

#: name -> FMInstrument. This is the bank a model picks from.
BANK = {
    "bass": BASS,
    "distorted_lead": DISTORTED_LEAD,
    "bell_pluck": BELL_PLUCK,
    "jazz_chord_pad": JAZZ_CHORD_PAD,
    "sub_bass": SUB_BASS,
    "slap_bass": SLAP_BASS,
    "deep_bass": DEEP_BASS,
    "techno_bass": TECHNO_BASS,
    "brass": BRASS,
    "e_piano": E_PIANO,
    "organ": ORGAN,
    "square_lead": SQUARE_LEAD,
    "pluck_guitar": PLUCK_GUITAR,
    "strings": STRINGS,
    "orch_hit": ORCH_HIT,
    "metal_stab": METAL_STAB,
    "saw_lead": SAW_LEAD,
    "hard_pluck": HARD_PLUCK,
    "fm_stab": FM_STAB,
}

#: One line per voice, for prompts and `--list`. Kept next to the patches
#: so a new instrument without a description is obvious in review.
CHARACTER = {
    "bass": "punchy FM bass, fast attack, short body",
    "distorted_lead": "gritty overdriven lead, heavy feedback, cuts through",
    "bell_pluck": "bright metallic pluck/bell, good for arpeggios",
    "jazz_chord_pad": "soft additive chord tone, one note per channel",
    "sub_bass": "pure sine sub, no harmonics, sits under everything",
    "slap_bass": "snappy funk bass with a bright attack transient",
    "deep_bass": "round mid-weight bass, harmonics fade slowly, sits under a mix",
    "techno_bass": "driving buzzy bass with stacked harmonics, four-on-the-floor",
    "brass": "FM brass section, brightens after the attack",
    "e_piano": "DX-style electric piano, bell-tinged, decays away",
    "organ": "additive drawbar organ, sustains flat until note off",
    "square_lead": "hard square-ish lead, chiptune-forward",
    "pluck_guitar": "muted guitar pluck, quick decay",
    "strings": "slow-attack detuned ensemble pad",
    "orch_hit": "short inharmonic orchestra-hit stab",
    "metal_stab": "clangorous industrial stab, very inharmonic",
    "saw_lead": "additive sawtooth lead, same bright shape at every pitch",
    "hard_pluck": "hard attack, fast decay, long ringing release",
    "fm_stab": "short metallic pitched stab for off-beats",
}


def _apply_calibration():
    """Load the generated loudness trims onto the bank.

    Kept in a separate generated module rather than inline in the patch
    definitions: the patches are hand-authored and the trims are measured,
    and mixing the two in one file means a calibration run rewrites human
    work. Missing file is not an error — an uncalibrated bank still plays.
    """
    try:
        from bank_calibration import TRIMS
    except ImportError:
        return
    for name, trim in TRIMS.items():
        patch = BANK.get(name)
        if patch is not None:
            patch.trim = trim


_apply_calibration()


def names():
    return sorted(BANK)


def get(name: str) -> FMInstrument:
    try:
        return BANK[name]
    except KeyError:
        raise KeyError(f"unknown instrument {name!r}; have: "
                       f"{', '.join(names())}") from None


def add(instrument: FMInstrument, character: str = "") -> FMInstrument:
    """Register a patch under its own name so events can reference it."""
    if not instrument.name:
        raise ValueError("instrument needs a name to go in the bank")
    BANK[instrument.name] = instrument
    if character:
        CHARACTER[instrument.name] = character
    return instrument


def describe() -> dict:
    """name -> {algorithm, feedback, character}. Feeds prompts and manifests."""
    return {
        name: {
            "algorithm": inst.algorithm,
            "feedback": inst.feedback,
            "carriers": len(inst.carrier_indices()),
            "trim": inst.trim,
            "character": CHARACTER.get(name, ""),
        }
        for name, inst in sorted(BANK.items())
    }


# --------------------------------------------------------------------------
# Serialisation — so a model can ship its own patches with a score
# --------------------------------------------------------------------------
_OP_FIELDS = ("detune", "multiple", "total_level", "attack_rate", "decay_rate",
              "sustain_rate", "release_rate", "sustain_level", "ssg_eg",
              "rate_scaling", "am_enable")


def instrument_to_dict(inst: FMInstrument) -> dict:
    return {
        "name": inst.name,
        "algorithm": inst.algorithm,
        "feedback": inst.feedback,
        "trim": inst.trim,
        "operator_order": "register",   # op1, op3, op2, op4 — see module docstring
        "operators": [{f: getattr(op, f) for f in _OP_FIELDS}
                      for op in inst.operators],
    }


def instrument_from_dict(d: dict) -> FMInstrument:
    """Rebuild a patch. `operator_order` may be "register" (op1,op3,op2,op4)
    or "chip"/"natural" (op1,op2,op3,op4) — stated explicitly because
    guessing wrong swaps two operators and quietly ruins the timbre."""
    ops = [Operator(**{f: op.get(f, Operator().__getattribute__(f))
                       for f in _OP_FIELDS})
           for op in d["operators"]]
    if len(ops) != 4:
        raise ValueError("an FM patch needs exactly 4 operators")
    order = str(d.get("operator_order", "register")).lower()
    if order in ("chip", "natural", "op1234"):
        ops = [ops[0], ops[2], ops[1], ops[3]]
    elif order != "register":
        raise ValueError(f"unknown operator_order {order!r}")
    return FMInstrument(int(d["algorithm"]), int(d["feedback"]), ops,
                        d.get("name", ""), int(d.get("trim", 0)))


def save_bank(path: str, bank: dict = None) -> str:
    data = [instrument_to_dict(i) for i in (bank or BANK).values()]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def load_bank(path: str, merge: bool = True) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    loaded = {}
    for entry in data:
        inst = instrument_from_dict(entry)
        loaded[inst.name] = inst
    if merge:
        BANK.update(loaded)
    return loaded
