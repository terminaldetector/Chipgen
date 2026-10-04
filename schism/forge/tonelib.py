"""tonelib.py — the arithmetic of a tone: from dials to partials.

`descriptors.py` says what a dial *means* by reading a rendered note. This
module goes the other way round: given a dial, what spectrum has a note
that reads that way. It uses the same rules the descriptors use, applied to
a list of (ratio, amplitude) partials instead of to an FFT, so that the
first instrument a genome compiles to is already near its target and the
closed loop in evolve.py has a correction to make, not a search to run.

Nothing here renders audio.
"""

import math

from . import descriptors as D

#: ratios of the partials a tone is built from, at most
MAX_PARTIALS = 96
LN16 = math.log(D.BRIGHT_HIGH / D.BRIGHT_LOW)


def clip(x, low=0.0, high=1.0):
    return low if x < low else high if x > high else x


# -- dial <-> physical value (the inverses of descriptors' scales) ----------------
def attack_seconds(dial: float) -> float:
    """Measured rise time (10% to 90%) for an attack dial."""
    return D.ATTACK_REFERENCE * ((1.0 + D.ATTACK_LONG / D.ATTACK_REFERENCE)
                                 ** dial - 1.0)


def sustain_ratio(body: float) -> float:
    """Level of the body against the peak for a body dial (-40 dB .. 0 dB)."""
    return 10.0 ** (D.BODY_FLOOR_DB * (1.0 - body) / 20.0)


def decay_seconds(dial: float) -> float:
    """Time constant of the fall from peak to body for a decay dial."""
    return D.DECAY_SHORT * (D.DECAY_LONG / D.DECAY_SHORT) ** dial


def release_seconds(dial: float) -> float:
    """Time to fall 20 dB after key-off for an articulation dial."""
    return D.RELEASE_SHORT * (D.RELEASE_LONG / D.RELEASE_SHORT) ** dial


def bass_ratio(dial: float) -> float:
    return D.BASS_RATIO_LOW * (D.BASS_RATIO_HIGH / D.BASS_RATIO_LOW) ** dial


def flatness(dial: float) -> float:
    return D.FLATNESS_LOW * (D.FLATNESS_HIGH / D.FLATNESS_LOW) ** dial


# -- the partial patterns --------------------------------------------------------
SHAPES = ("saw", "square", "pulse", "organ", "reed", "vowel")

_DRAWBARS = (1, 2, 3, 4, 6, 8, 10, 12, 16)


def weights(shape: str, count: int):
    """w_h for h = 1..count: which partials a shape has, before the
    brightness law h**-p is applied."""
    out = []
    for h in range(1, count + 1):
        if shape == "saw":
            w = 1.0
        elif shape == "square":
            w = 1.0 if h % 2 else 0.0
        elif shape == "pulse":
            w = abs(math.sin(math.pi * h * 0.25)) * 1.4142
        elif shape == "organ":
            w = 1.0 if h in _DRAWBARS else 0.0
        elif shape == "reed":
            # odd partials strong, even ones present: a clarinet that has
            # been lied to; a bump near the fifth partial
            w = (1.0 if h % 2 else 0.35) * (1.0 + 1.6 * math.exp(
                -((math.log(h / 5.0)) ** 2) / (2 * 0.22 ** 2)))
        elif shape == "vowel":
            w = (1.0 + 2.2 * math.exp(-((math.log(h / 3.0)) ** 2) / (2 * 0.2 ** 2))
                 + 1.6 * math.exp(-((math.log(h / 9.0)) ** 2) / (2 * 0.2 ** 2)))
        else:
            raise ValueError(f"no tone shape {shape!r}; have "
                             f"{', '.join(SHAPES)}")
        out.append(w)
    if not any(out):
        out[0] = 1.0
    return out


def partials_of(shape: str, p: float, count: int, g1: float = 1.0,
                rough: float = 0.0, stretch: float = 0.0):
    """[(ratio, amplitude)]: shape weights x h**-p, the fundamental scaled
    by `g1`, an alternating roughness of depth `rough` from the 4th partial
    up, and partial h moved to ratio h*(1 + stretch*(h-1))."""
    w = weights(shape, count)
    out = []
    for h in range(1, count + 1):
        a = w[h - 1] * h ** (-p)
        if h == 1:
            a *= g1
        if rough and h >= 4:
            a *= 1.0 + rough * (1.0 if h % 2 == 0 else -1.0)
        if a <= 1e-9:
            continue
        ratio = h * (1.0 + stretch * (h - 1))
        out.append((ratio, a))
    return out


# -- the descriptors' own formulas, on a partial list --------------------------------
def centroid_ratio(partials) -> float:
    """Spectral centroid over the fundamental, as `_profile` reads it:
    magnitude-weighted, partials under 1% of the loudest left out."""
    top = max((a for _, a in partials), default=0.0)
    if top <= 0:
        return 1.0
    floor = 0.01 * top
    num = den = 0.0
    for r, a in partials:
        if a > floor:
            num += a * r
            den += a
    return max(1.0, num / den) if den else 1.0


def inharmonicity(partials) -> float:
    """Weighted distance of the partials from whole multiples."""
    top = max((a for _, a in partials), default=0.0)
    if top <= 0:
        return 0.0
    floor = 0.08 * top
    inharm = total = 0.0
    for r, a in partials:
        if a < floor or r < 0.6:
            continue
        inharm += a * abs(r - round(r)) * 2.0
        total += a
    return inharm / total if total else 0.0


def harmonic_vector(partials, count: int = D.HARMONICS):
    """Amplitude read at each of the first `count` multiples, relative to
    the loudest, as the descriptors read the harmonic profile (a window of
    +-3% about each multiple)."""
    amps = [0.0] * count
    for r, a in partials:
        k = int(round(r))
        if 1 <= k <= count and abs(r - k) <= 0.03 * k:
            amps[k - 1] = max(amps[k - 1], a)
    top = max(amps) or 1.0
    return [a / top for a in amps]


def roughness(partials) -> float:
    h = harmonic_vector(partials)
    energy = sum(v * v for v in h) or 1.0
    upper = sum(v * v for v in h[4:]) / energy
    if upper <= 0.01:
        return 0.0
    irregular = 0.0
    for k in range(3, 22):
        nbr = (h[k - 1] + h[k] + h[k + 1]) / 3.0
        irregular += abs(h[k] - nbr)
    rough = irregular / (sum(h) or 1.0)
    return min(1.0, rough) * min(1.0, upper / 0.25)


def bass_energy_ratio(partials) -> float:
    """Energy at or under 1.5 f0 against 1.5-4.5 f0."""
    low = sum(a * a for r, a in partials if 0.35 <= r <= 1.5)
    mid = sum(a * a for r, a in partials if 1.5 < r <= 4.5)
    return low / (mid + 1e-18)


def metallicity_of(partials) -> float:
    return clip(0.5 * clip(inharmonicity(partials) / D.METAL_INHARM_FULL)
                + 0.5 * clip(roughness(partials) / D.METAL_ROUGH_FULL))


# -- solving for a dial -----------------------------------------------------------------
def _bisect(fn, low, high, target, rising=True, steps=40):
    """Solve fn(x) = target on [low, high] for a monotone fn."""
    flo, fhi = fn(low), fn(high)
    if rising:
        if target <= flo:
            return low
        if target >= fhi:
            return high
    else:
        if target >= flo:
            return low
        if target <= fhi:
            return high
    for _ in range(steps):
        mid = 0.5 * (low + high)
        v = fn(mid)
        if (v < target) == rising:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high)


def design(shape: str, count: int, brightness_ratio: float,
           bass_ratio_target: float, metal_inharm: float = 0.0,
           metal_rough: float = 0.0):
    """-> (partials, p, g1, stretch, rough): a spectrum whose centroid is
    `brightness_ratio` x the fundamental and whose bass ratio is
    `bass_ratio_target`, with the requested inharmonicity and roughness.

    The brightness law p and the fundamental's gain g1 depend on each other
    (the fundamental is part of the centroid), so they are solved together
    by a few passes."""
    # stretch and roughness first: they change the partial positions and
    # weights the centroid is read from
    p, g1 = 1.0, 1.0
    stretch = rough = 0.0
    for _ in range(5):
        def centroid_of_p(x):
            return centroid_ratio(partials_of(shape, x, count, g1, rough,
                                              stretch))
        p = _bisect(centroid_of_p, -1.0, 14.0, brightness_ratio, rising=False)
        base = partials_of(shape, p, count, 1.0, rough, stretch)
        mid = sum(a * a for r, a in base if 1.5 < r <= 4.5)
        a1 = max((a for r, a in base if r < 1.5), default=1.0)
        g1 = clip(math.sqrt(bass_ratio_target * mid) / a1 if mid > 0 else 1.0,
                  0.02, 60.0)
        if metal_inharm > 0.0:
            stretch = _bisect(
                lambda s: inharmonicity(partials_of(shape, p, count, g1,
                                                    rough, s)),
                0.0, 0.25, metal_inharm)
        if metal_rough > 0.0:
            rough = _bisect(
                lambda r: roughness(partials_of(shape, p, count, g1, r,
                                                stretch)),
                0.0, 0.95, metal_rough)
    partials = partials_of(shape, p, count, g1, rough, stretch)
    return partials, p, g1, stretch, rough


def transient(partials, shape: str, count: int, extra_octaves: float):
    """The partials of the first milliseconds of a punchy note: the same
    fundamental, and a top end `extra_octaves` higher in centroid than the
    body's. The body's other partials are not kept — a transient is its own
    spectrum (a sine body can have a bright click) — only the fundamental's
    level is, so the crossfade between the two leaves the pitch alone.
    -> list of (ratio, amplitude)."""
    if not partials:
        return []
    a1 = max((a for r, a in partials if r < 1.5), default=1.0)
    body_ratio = centroid_ratio(partials)
    target = body_ratio * 2.0 ** extra_octaves
    w = weights(shape, count)

    def built(p):
        out = [(1.0, a1)]
        for h in range(2, count + 1):
            a = a1 * w[h - 1] * h ** (-p)
            if a > 1e-9:
                out.append((float(h), a))
        return out
    p = _bisect(lambda x: centroid_ratio(built(x)), -1.0, 14.0, target,
                rising=False)
    return built(p)
