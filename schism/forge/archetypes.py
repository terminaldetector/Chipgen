"""archetypes.py — named starting points, one table, readable and editable.

An archetype is a DNA, a family and a register: "a bright, metallic,
ringing bell, to be heard around C5". It is where a search begins when
nobody gives it a better place, and where a model starts when it says
"something like a slap bass, but". Every number is an opinion about what
that kind of sound measures; `python3 -m schism forge survey` prints what
the real ones do, so an argument about one is settled by a render.

Drums and effects are here too. Their register is the pitch they are
*tuned* to (a kick at A1, a hi-hat at A5): the dials read a sound against
its fundamental, and an unpitched sound has the pitch it is tuned to.

Body and decay are not independent. Body is the level 0.6 s into the note,
and decay is how long the fall from the peak to that level takes, so a
decay longer than about half a second needs a body near the top (and then
reads 1.0: it hardly falls). The table keeps to what a note can measure.
"""

from collections import namedtuple

from .dna import DNA

Archetype = namedtuple("Archetype", "family dna register roles doc style")

A = Archetype
ARCHETYPES = {
    # -- tone: basses ------------------------------------------------------
    "sub_bass": A("tone", DNA(brightness=0.04, attack=0.05, body=0.9,
                              decay=0.7, metallicity=0.0, bass_weight=1.0,
                              articulation=0.35), ("A", 2), ("bass",),
                  "a pure sine under everything", {}),
    "punch_bass": A("tone", DNA(brightness=0.55, attack=0.0, body=0.6,
                                decay=0.45, metallicity=0.12, bass_weight=0.8,
                                articulation=0.3, punch=0.35), ("A", 2),
                    ("bass",), "a punchy bass with a bite at the front", {}),
    "slap_bass": A("tone", DNA(brightness=0.5, attack=0.0, body=0.3,
                               decay=0.3, metallicity=0.2, bass_weight=0.6,
                               articulation=0.15, punch=0.85), ("A", 2),
                   ("bass",), "a funk bass: bright attack, clean body", {}),
    "acid_bass": A("tone", DNA(brightness=0.8, attack=0.0, body=0.45,
                               decay=0.35, metallicity=0.15, bass_weight=0.65,
                               articulation=0.2, punch=0.6), ("A", 2),
                   ("bass",), "a squelchy saw bass that closes as it falls",
                   {"shape": "saw"}),
    "wobble_bass": A("tone", DNA(brightness=0.6, attack=0.02, body=0.9,
                                 decay=0.9, metallicity=0.1, detune=0.3,
                                 motion=0.8, bass_weight=0.85,
                                 articulation=0.3), ("A", 1), ("bass",),
                     "a sustained bass with a fast tremolo",
                     {"motion_kind": "tremolo"}),
    # -- tone: leads ---------------------------------------------------------
    "saw_lead": A("tone", DNA(brightness=0.72, attack=0.03, body=0.85,
                              decay=0.6, metallicity=0.08, detune=0.2,
                              motion=0.3, bass_weight=0.1,
                              articulation=0.6), ("A", 4), ("lead",),
                  "a sawtooth lead with a little ensemble and vibrato", {}),
    "square_lead": A("tone", DNA(brightness=0.5, attack=0.03, body=0.9,
                                 decay=0.7, metallicity=0.05, motion=0.2,
                                 bass_weight=0.15, articulation=0.5),
                     ("A", 4), ("lead",), "the chiptune square",
                     {"shape": "square"}),
    "supersaw": A("tone", DNA(brightness=0.8, attack=0.02, body=0.95,
                              decay=1.0, metallicity=0.1, detune=0.9,
                              motion=0.1, bass_weight=0.2,
                              articulation=0.55), ("A", 3),
                  ("lead", "pad"), "a wall of detuned saws", {"unison": 3}),
    "whistle": A("tone", DNA(brightness=0.05, attack=0.2, body=0.95,
                             decay=1.0, metallicity=0.0, motion=0.6,
                             bass_weight=0.3, articulation=0.4,
                             noise=0.15), ("A", 5), ("lead",),
                 "a near-sine with breath and vibrato", {}),
    "reed": A("tone", DNA(brightness=0.55, attack=0.12, body=0.85,
                          decay=0.6, metallicity=0.1, motion=0.35,
                          bass_weight=0.35, articulation=0.4, noise=0.1),
              ("A", 3), ("lead", "harmony"), "a clarinet-ish reed",
              {"shape": "reed"}),
    "brass": A("tone", DNA(brightness=0.62, attack=0.28, body=0.85,
                           decay=0.5, metallicity=0.1, detune=0.15,
                           motion=0.25, bass_weight=0.3, articulation=0.45,
                           punch=0.2), ("A", 3), ("lead", "harmony"),
               "a section that leans in after the attack", {}),
    "choir": A("tone", DNA(brightness=0.4, attack=0.5, body=0.95,
                           decay=1.0, metallicity=0.05, detune=0.5,
                           motion=0.4, bass_weight=0.3, articulation=0.8,
                           noise=0.1), ("A", 3), ("pad", "harmony"),
               "a vowel pad", {"shape": "vowel"}),
    # -- tone: keys, organs, pads ----------------------------------------------
    "epiano": A("tone", DNA(brightness=0.4, attack=0.0, body=0.35,
                            decay=0.55, metallicity=0.3, detune=0.2,
                            motion=0.15, bass_weight=0.3, articulation=0.55,
                            punch=0.3), ("A", 3), ("harmony", "lead"),
                "an electric piano that rings and fades", {}),
    "organ": A("tone", DNA(brightness=0.45, attack=0.02, body=1.0,
                           decay=1.0, metallicity=0.1, motion=0.3,
                           bass_weight=0.4, articulation=0.15),
               ("A", 3), ("harmony", "pad"),
               "a drawbar organ that holds until the key comes up",
               {"shape": "organ", "motion_kind": "tremolo"}),
    "pad": A("tone", DNA(brightness=0.3, attack=0.65, body=0.95, decay=1.0,
                         metallicity=0.05, detune=0.7, motion=0.5,
                         bass_weight=0.25, articulation=0.9), ("A", 3),
             ("pad", "harmony"), "a slow swelling ensemble pad", {}),
    "strings": A("tone", DNA(brightness=0.5, attack=0.55, body=0.95,
                             decay=1.0, metallicity=0.05, detune=0.75,
                             motion=0.3, bass_weight=0.2, articulation=0.8,
                             noise=0.05), ("A", 3), ("pad", "harmony"),
                 "bowed strings: slow, wide, a little rough", {}),
    "glass_pad": A("tone", DNA(brightness=0.6, attack=0.6, body=0.95,
                               decay=1.0, metallicity=0.45, detune=0.5,
                               motion=0.4, bass_weight=0.1, articulation=0.9),
                   ("A", 4), ("pad",), "a cold, slightly inharmonic pad",
                   {}),
    "stab": A("tone", DNA(brightness=0.7, attack=0.0, body=0.05, decay=0.2,
                          metallicity=0.5, bass_weight=0.2, articulation=0.1,
                          punch=0.7), ("A", 3), ("harmony",),
              "a short metallic hit for the off-beat", {}),
    "blip": A("tone", DNA(brightness=0.6, attack=0.0, body=0.1, decay=0.15,
                          metallicity=0.1, bass_weight=0.1, articulation=0.05,
                          punch=0.8), ("A", 4), ("lead",),
              "an arcade blip with a chirp", {}),
    # -- modal: struck and plucked things ----------------------------------------
    "pluck": A("modal", DNA(brightness=0.5, attack=0.0, body=0.05,
                            decay=0.28, metallicity=0.05, bass_weight=0.3,
                            articulation=0.2, punch=0.3), ("A", 3),
               ("harmony", "lead"), "a plucked string", {"set": "string"}),
    "harp": A("modal", DNA(brightness=0.45, attack=0.0, body=0.3, decay=0.45,
                           metallicity=0.05, detune=0.1, bass_weight=0.25,
                           articulation=0.6, punch=0.35), ("A", 3),
              ("harmony",), "a harp: bright start, long gentle ring",
              {"set": "string"}),
    "marimba": A("modal", DNA(brightness=0.3, attack=0.0, body=0.02,
                              decay=0.3, metallicity=0.4, bass_weight=0.4,
                              articulation=0.2, punch=0.5), ("A", 3),
                 ("lead", "harmony"), "a wooden bar, fundamental and a tenth",
                 {"set": "bar"}),
    "vibes": A("modal", DNA(brightness=0.35, attack=0.0, body=0.5,
                            decay=0.55, metallicity=0.4, motion=0.55,
                            bass_weight=0.3, articulation=0.6, punch=0.3),
               ("A", 3), ("lead", "harmony"),
               "a metal bar with a motor tremolo", {"set": "bar"}),
    "bell": A("modal", DNA(brightness=0.75, attack=0.0, body=0.55,
                           decay=0.5, metallicity=0.8, detune=0.1,
                           motion=0.0, bass_weight=0.05, articulation=0.8,
                           punch=0.2), ("A", 4), ("lead", "harmony"),
              "a bell: bright, metallic, rings for seconds",
              {"set": "bell"}),
    "glass": A("modal", DNA(brightness=0.65, attack=0.0, body=0.5,
                            decay=0.5, metallicity=0.6, bass_weight=0.05,
                            articulation=0.7, punch=0.15), ("A", 5),
               ("lead",), "a struck glass", {"set": "plate"}),
    "kalimba": A("modal", DNA(brightness=0.4, attack=0.0, body=0.05,
                              decay=0.4, metallicity=0.35, bass_weight=0.3,
                              articulation=0.4, punch=0.5), ("A", 4),
                 ("lead",), "a thumb piano: dull thunk, clear ring",
                 {"set": "bar"}),
    "piano": A("modal", DNA(brightness=0.55, attack=0.0, body=0.35,
                            decay=0.5, metallicity=0.15, detune=0.35,
                            bass_weight=0.35, articulation=0.5, punch=0.45),
               ("A", 3), ("harmony", "lead"),
               "a struck string pair: bright thump, slow beating fall",
               {"set": "string"}),
    # -- drums: tuned to a pitch ---------------------------------------------------
    "kick": A("drum", DNA(brightness=0.15, attack=0.0, body=0.0, decay=0.35,
                          bass_weight=0.95, articulation=0.1, punch=0.6),
              ("A", 1), ("drums",), "a kick: sine falling from a click",
              {"kind": "kick"}),
    "kick_hard": A("drum", DNA(brightness=0.3, attack=0.0, body=0.0,
                               decay=0.4, bass_weight=0.9, articulation=0.1,
                               punch=0.9), ("A", 1), ("drums",),
                   "a hard kick with a long beater click",
                   {"kind": "kick"}),
    "snare": A("drum", DNA(brightness=0.85, attack=0.0, body=0.0, decay=0.3,
                           metallicity=0.5, bass_weight=0.15, articulation=0.1,
                           noise=0.7, punch=0.5), ("G", 4), ("drums",),
               "a snare: noise with a crack and a body", {"kind": "snare"}),
    "hat": A("drum", DNA(brightness=0.85, attack=0.0, body=0.0, decay=0.12,
                         metallicity=0.65, bass_weight=0.0, articulation=0.05,
                         noise=0.75), ("A", 5), ("drums",), "a closed hat",
             {"kind": "hat"}),
    "open_hat": A("drum", DNA(brightness=0.85, attack=0.0, body=0.0,
                              decay=0.4, metallicity=0.65, bass_weight=0.0,
                              articulation=0.05, noise=0.75), ("A", 5),
                  ("drums",), "an open hat", {"kind": "hat"}),
    "clap": A("drum", DNA(metallicity=0.6, brightness=0.9, attack=0.5, body=0.0, decay=0.3,
                          bass_weight=0.1, articulation=0.1, noise=0.7,
                          punch=0.05), ("D", 4), ("drums",),
              "a hand clap: four bursts and a tail", {"kind": "clap"}),
    "tom": A("drum", DNA(metallicity=0.36, brightness=0.06, attack=0.0, body=0.4, decay=0.42,
                         bass_weight=1.0, articulation=0.1, punch=0.4),
             ("A", 2), ("drums",), "a tom: sine falling gently",
             {"kind": "tom"}),
    "rim": A("drum", DNA(brightness=0.35, attack=0.0, body=0.0, decay=0.0,
                         metallicity=0.4, bass_weight=0.1, articulation=0.05,
                         punch=0.0), ("A", 4), ("drums",),
             "a rim click: two short tones", {"kind": "rim"}),
    "cymbal": A("drum", DNA(brightness=0.9, attack=0.0, body=0.7,
                            decay=0.5, metallicity=0.66, bass_weight=0.0,
                            articulation=0.2, noise=0.75), ("A", 5),
                ("drums",), "a crash: a long wash of metal and noise",
                {"kind": "cymbal"}),
    "shaker": A("drum", DNA(metallicity=0.6, brightness=0.8, attack=0.5, body=0.0,
                            decay=0.15, bass_weight=0.0, articulation=0.05,
                            noise=0.85), ("A", 5), ("drums",),
                "a shaker: a soft burst of filtered noise",
                {"kind": "shaker"}),
    "cowbell": A("drum", DNA(brightness=0.55, attack=0.0, body=0.0,
                             decay=0.3, metallicity=0.6, bass_weight=0.0,
                             articulation=0.1, punch=0.0), ("G", 4),
                 ("drums",), "two square tones through a band-pass",
                 {"kind": "cowbell"}),
    # -- effects -----------------------------------------------------------------------
    "riser": A("fx", DNA(brightness=0.85, attack=0.95, body=0.0, decay=0.05,
                         metallicity=0.58, bass_weight=0.1, articulation=0.1,
                         noise=0.6), ("A", 3), ("fx",),
               "noise rising in a sweep, then cut", {"kind": "riser"}),
    "impact": A("fx", DNA(brightness=0.3, attack=0.0, body=0.6, decay=0.5,
                          bass_weight=1.0, articulation=0.2, noise=0.0,
                          punch=0.5), ("A", 1), ("fx",),
                "a boom: a falling sine and a noise burst",
                {"kind": "impact"}),
    "zap": A("fx", DNA(noise=0.45, brightness=0.7, attack=0.0, body=0.0, decay=0.25,
                       metallicity=0.5, bass_weight=0.5, articulation=0.1,
                       punch=0.35), ("A", 4), ("fx",),
             "a laser: a fast fall in pitch", {"kind": "zap"}),
    "swoosh": A("fx", DNA(metallicity=0.55, brightness=0.9, attack=0.95, body=0.85, decay=0.5,
                          bass_weight=0.1, articulation=0.1, noise=0.65),
                ("A", 3), ("fx",), "a pass of noise, up and away",
                {"kind": "swoosh"}),
}


def names():
    return sorted(ARCHETYPES)


def get(name: str) -> Archetype:
    try:
        return ARCHETYPES[name]
    except KeyError:
        raise KeyError(f"unknown archetype {name!r}; have: "
                       f"{', '.join(names())}") from None


def dna_of(name: str) -> DNA:
    return get(name).dna


def for_role(role: str):
    """Archetype names that can play a role."""
    return [n for n, a in sorted(ARCHETYPES.items()) if role in a.roles]


def for_family(family: str):
    return [n for n, a in sorted(ARCHETYPES.items()) if a.family == family]
