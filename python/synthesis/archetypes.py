"""archetypes.py — named starting points, one table, readable and editable.

An archetype is a DNA and a register: "a bright, metallic, ringing bell, to
be heard around C5". It is where a search begins when nobody gives it a
better place, and where a model starts when it says "something like a
slap bass, but". Every number is an opinion about what that kind of sound
measures; `python3 python/forge.py survey` prints what the real ones do, so
an argument about one is settled by a render.
"""

from .dna import DNA

#: name -> (DNA, probe register, which roles it can play, one line)
ARCHETYPES = {
    "sub_bass": (DNA(brightness=0.04, attack=0.05, body=0.9, decay=0.7,
                     metallicity=0.0, detune=0.0, motion=0.0, bass_weight=1.0,
                     articulation=0.35), ("A", 2), ("bass",),
                 "a pure sine under everything"),
    "punch_bass": (DNA(brightness=0.55, attack=0.0, body=0.6, decay=0.45,
                    metallicity=0.12, detune=0.0, motion=0.0, bass_weight=0.8,
                    articulation=0.3, punch=0.35), ("A", 2), ("bass",),
                "a punchy bass with a bite at the front"),
    "slap_bass": (DNA(brightness=0.5, attack=0.0, body=0.3, decay=0.3,
                      metallicity=0.2, bass_weight=0.6, articulation=0.15,
                      punch=0.85), ("A", 2), ("bass",),
                  "a funk bass: bright attack, clean body"),
    "saw_lead": (DNA(brightness=0.72, attack=0.03, body=0.85, decay=0.6,
                     metallicity=0.08, detune=0.2, motion=0.3,
                     bass_weight=0.1, articulation=0.6), ("A", 4), ("lead",),
                 "a sawtooth lead with a little ensemble and vibrato"),
    "square_lead": (DNA(brightness=0.5, attack=0.03, body=0.9, decay=0.7,
                        metallicity=0.05, motion=0.2, bass_weight=0.15,
                        articulation=0.5), ("A", 4), ("lead",),
                    "the chiptune square"),
    "epiano": (DNA(brightness=0.4, attack=0.0, body=0.35, decay=0.55,
                   metallicity=0.3, detune=0.2, motion=0.15, bass_weight=0.3,
                   articulation=0.55), ("A", 3), ("harmony", "lead"),
               "an electric piano that rings and fades"),
    "bell": (DNA(brightness=0.75, attack=0.0, body=0.15, decay=0.8,
                 metallicity=0.8, detune=0.1, motion=0.1, bass_weight=0.05,
                 articulation=0.8), ("A", 4), ("lead", "harmony"),
             "a bell: bright, metallic, rings for seconds"),
    "pluck": (DNA(brightness=0.5, attack=0.0, body=0.05, decay=0.28,
                  metallicity=0.2, bass_weight=0.3, articulation=0.2,
                  punch=0.3), ("A", 3), ("harmony", "lead"),
              "a plucked string"),
    "pad": (DNA(brightness=0.3, attack=0.65, body=0.85, decay=0.8,
                metallicity=0.05, detune=0.7, motion=0.5, bass_weight=0.25,
                articulation=0.9), ("A", 3), ("pad", "harmony"),
            "a slow swelling ensemble pad"),
    "organ": (DNA(brightness=0.45, attack=0.02, body=1.0, decay=1.0,
                  metallicity=0.1, motion=0.3, bass_weight=0.4,
                  articulation=0.15), ("A", 3), ("harmony", "pad"),
              "a drawbar organ that holds until the key comes up"),
    "brass": (DNA(brightness=0.62, attack=0.28, body=0.85, decay=0.5,
                  metallicity=0.1, detune=0.15, motion=0.25, bass_weight=0.3,
                  articulation=0.45), ("A", 3), ("lead", "harmony"),
              "a section that leans in after the attack"),
    "stab": (DNA(brightness=0.7, attack=0.0, body=0.05, decay=0.2,
                 metallicity=0.5, bass_weight=0.2, articulation=0.1,
                 punch=0.7), ("A", 3), ("harmony",),
             "a short metallic hit for the off-beat"),
    "blip": (DNA(brightness=0.6, attack=0.0, body=0.1, decay=0.15,
                 metallicity=0.1, bass_weight=0.1, articulation=0.05,
                 punch=0.8), ("A", 4), ("lead",),
             "an arcade blip with a chirp"),
    "noise_hat": (DNA(brightness=0.95, attack=0.0, body=0.0, decay=0.12,
                      metallicity=0.4, bass_weight=0.0, articulation=0.05,
                      noise=0.95), ("A", 4), ("drums",),
                  "a closed hat"),
    "noise_snare": (DNA(brightness=0.6, attack=0.0, body=0.0, decay=0.3,
                        metallicity=0.2, bass_weight=0.3, articulation=0.1,
                        noise=0.9, punch=0.6), ("A", 3), ("drums",),
                    "a snare: noise with a crack"),
    "noise_kick": (DNA(brightness=0.2, attack=0.0, body=0.0, decay=0.35,
                       bass_weight=0.9, articulation=0.1, noise=0.8,
                       punch=0.6), ("A", 2), ("drums",),
                   "a noise kick: low period, fast fall"),
}


def names():
    return sorted(ARCHETYPES)


def get(name: str):
    try:
        return ARCHETYPES[name]
    except KeyError:
        raise KeyError(f"unknown archetype {name!r}; have: "
                       f"{', '.join(names())}") from None


def dna_of(name: str) -> DNA:
    return get(name)[0]


def for_role(role: str):
    """Archetype names that can play a role."""
    return [n for n, (_, _, roles, _) in sorted(ARCHETYPES.items())
            if role in roles]
