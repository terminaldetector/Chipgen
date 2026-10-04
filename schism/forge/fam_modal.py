"""fam_modal.py — the modal family: damped partials, each with its own fall.

Plucked strings, harps, pianos, marimbas, vibraphones, bells, glass,
kalimbas. The sample is a one-shot (`modal.py`): nothing loops, because
nothing in this family is steady, it only rings.

What each dial does to the compiled instrument:

    brightness   the roll-off of the initial partial amplitudes, solved so
                 the centroid sits at 16**brightness times the fundamental
    decay        the fall of the fundamental; the other partials fall
                 faster by their ratio to the power of the tilt
    punch        the tilt: how much faster the top end falls than the
                 bottom, so the sound darkens as it rings
    metallicity  which set of ratios (strings stiffened, a tuned bar, a
                 free bar, a bell, a plate) and how loud the partials that
                 are not whole multiples are
    detune       every mode is a close pair, beating at a fixed number of
                 cents (a piano string's three strings)
    motion       tremolo (a vibraphone's fan)
    noise        a burst of noise at the strike (a hammer, a pick)
    articulation the volume envelope's release (a damper)
    attack       a ramp in the sample
"""

import math

from . import descriptors as D, modal, spectral, tonelib as T
from .family import Compiled, Family, register_family
from .fam_tone import _amp_envelope, register_hz
from .genome import Genome
from .patch import Patch, REFERENCE_TICK, register_sampler

MAX_SECONDS = 2.4
#: seconds a note rings, in the sample, at least
MIN_SECONDS = 0.15
TILT_BASE, TILT_PER_PUNCH = 0.15, 1.7
#: how deep the beat of a mode pair reads for a given level (measured)
PAIR_GAIN = 1.7
TREMOLO_HZ = 5.5


class ModalFamily(Family):
    name = "modal"
    doc = ("damped partials, each with its own fall: plucked strings, harps, "
           "pianos, marimbas, vibes, bells, glass")
    default_register = ("A", 3)
    genes = {"set": ["string", "tuned", "bar", "bell", "plate"]}
    pitched = True

    def pin(self, genome: Genome) -> Genome:
        style = dict(genome.style)
        if style.get("set") in (None, "auto"):
            m = genome.dna.metallicity
            style["set"] = "string" if m < 0.25 else "bar" if m < 0.55 \
                else "bell"
        return genome.clone(style=style)

    def compile(self, genome: Genome) -> Compiled:
        g = self.clamp_genome(self.pin(genome))
        d = g.effective()
        f0 = register_hz(g.register)
        kind = g.style["set"]
        notes = []

        # -- the partials -----------------------------------------------------
        ratios = list(modal.SETS[kind])
        if kind == "string":
            count = int(max(4, min(len(ratios), D.TOP_HZ / f0)))
            ratios = ratios[:count]
            # stiffness: 0 for a nylon string, 0.02 for a thick wound one
            stiff = 0.0001 + 0.02 * max(0.0, d.metallicity - 0.05) ** 1.5
            ratios = [h * math.sqrt(1.0 + stiff * h * h) for h in ratios]
        else:
            stiff = 0.0
        ratio_target = 16.0 ** d.brightness
        bass_target = T.bass_ratio(d.bass_weight)
        inharm_target = D.METAL_INHARM_FULL * min(1.0, 2.0 * d.metallicity)
        base = [1.0] * len(ratios)
        s_in = 1.0
        if kind != "string":
            def inharm_of(s):
                part = [(r, (1.0 if abs(r - round(r)) < 0.03 else s))
                        for r in ratios]
                return T.inharmonicity(part)
            s_in = T._bisect(inharm_of, 0.05, 3.0, inharm_target)
            base = [1.0 if abs(r - round(r)) < 0.03 else s_in for r in ratios]
        p, g1 = 1.0, 1.0
        for _ in range(4):
            def centroid_of(x):
                part = [(r, b * (r ** -x) * (g1 if i == 0 else 1.0))
                        for i, (r, b) in enumerate(zip(ratios, base))]
                return T.centroid_ratio(part)
            p = T._bisect(centroid_of, -1.0, 14.0, ratio_target, rising=False)
            amps = [b * r ** -p for r, b in zip(ratios, base)]
            mid = sum(a * a for r, a in zip(ratios, amps) if 1.5 < r <= 4.5)
            g1 = T.clip(math.sqrt(bass_target * mid) / amps[0]
                        if mid > 0 else 1.0, 0.02, 60.0)
        amps[0] *= g1
        notes.append(f"{kind} partials ({len(ratios)}) falling off as "
                     f"h^({-p:.2f}), fundamental x{g1:.2f}"
                     + (f", stiffness {stiff:.4f}" if stiff > 0.001 else "")
                     + (f", inharmonic partials x{s_in:.2f}"
                        if kind != "string" else ""))

        # -- the falls ----------------------------------------------------------
        tau1 = min(T.decay_seconds(d.decay), 2.0)
        tilt = TILT_BASE + TILT_PER_PUNCH * d.punch
        modes = []
        for r, a in zip(ratios, amps):
            tau = max(0.004, tau1 * r ** -tilt)
            modes.append([round(r, 5), round(a, 6), round(tau, 5)])
        length = min(MAX_SECONDS, max(MIN_SECONDS, 4.6 * tau1))
        notes.append(f"fundamental falls with tau {tau1 * 1000:.0f} ms, "
                     f"partial k with tau/k^{tilt:.2f}")
        params = {"modes": modes, "length": round(length, 3),
                  "attack_s": round(T.attack_seconds(d.attack) / 0.8, 5)}

        # -- pairs, tremolo, noise ------------------------------------------------
        if d.detune > 0.02:
            depth = (D.MOVEMENT_FLOOR + D.DETUNE_FULL * d.detune) * PAIR_GAIN
            rho = min(0.95, depth)
            beat = 1.3                              # Hz on the fundamental
            cents = 1200.0 * math.log2(1.0 + beat / f0)
            params["pair"] = {"cents": round(cents, 3), "level": round(rho, 4)}
            notes.append(f"every mode a pair {cents:.1f} cents apart at "
                         f"{rho:.2f}")
        if d.motion > 0.02:
            params["tremolo"] = {"depth": round(D.MOVEMENT_FLOOR
                                                + D.MOTION_AM_FULL * d.motion,
                                                4), "hz": TREMOLO_HZ}
            notes.append(f"tremolo {params['tremolo']['depth']:.2f} deep at "
                         f"{TREMOLO_HZ} Hz")
        if d.noise > 0.02:
            params["noise"] = {"level": round(0.05 + 1.2 * d.noise, 3),
                               "tau": 0.012, "hp": round(1.5 * f0)}
            notes.append("a burst of noise at the strike")

        # -- the instrument ----------------------------------------------------------
        t20 = T.release_seconds(d.articulation)
        amp, sus, _ = _amp_envelope(0.0, True, None, 1.0, True, t20)
        patch = Patch(self.name, params, amp=amp, amp_sustain=sus)
        patch.inst["nna"] = "fade" if d.articulation >= 0.55 else "cut"
        if patch.inst["nna"] == "fade":
            patch.inst["fade"] = int(max(1, min(256, round(
                2048.0 * REFERENCE_TICK / (2.0 * t20)))))
        patch.inst["gain"] = 128
        o = g.register[1]
        patch.bands = (max(1, 12 * (o - 2) + 1), min(120, 12 * (o + 3)), 15)
        return Compiled(self.name, g.name or "modal", patch, {},
                        {"pitched": True, "release_expected": True}, notes)

    def plain(self, compiled: Compiled):
        params = compiled.patch.params
        if "pair" not in params and "tremolo" not in params:
            return None
        out = Compiled(compiled.family, compiled.name, compiled.patch.clone(),
                       dict(compiled.setup), dict(compiled.style),
                       list(compiled.notes))
        out.patch.params.pop("pair", None)
        out.patch.params.pop("tremolo", None)
        return out


def modal_sampler(params: dict, low: int, high: int):
    base = (low + high) // 2
    f_base = spectral.hz_of_note(base)
    f_high = spectral.hz_of_note(high)
    modes = [m for m in params["modes"]
             if m[0] * f_high <= spectral.BAND_LIMIT_HZ]
    if not modes:
        modes = params["modes"][:1]
    top = max(m[0] for m in modes) * f_base
    rate = 22050 if top < 9000.0 else spectral.SAMPLE_RATE
    n = int(params["length"] * rate)
    pair = params.get("pair")
    full = []
    for i, (r, a, tau) in enumerate(modes):
        # modes start out of step (golden-ratio phases), which keeps the
        # strike from being a single spike that eats the sample's headroom
        phase = (0.37 + 0.618034 * i) % 1.0
        full.append((r, a, tau, phase))
        if pair:
            full.append((r * 2.0 ** (pair["cents"] / 1200.0), a * pair["level"],
                         tau, (phase + 0.31) % 1.0))
    x = modal.modes_sum(full, n, rate, f_base)
    noise = params.get("noise")
    peak = max((abs(v) for v in x), default=1.0) or 1.0
    if noise:
        burst = modal.noise_burst(min(n, int(0.08 * rate)), noise["tau"],
                                  rate, 5, noise["hp"] if noise["hp"] < rate / 3
                                  else 0.0)
        for i, v in enumerate(burst):
            x[i] += v * noise["level"] * peak * 0.5
    modal.ramp_in(x, max(2, int(round(params.get("attack_s", 0.0) * rate))))
    trem = params.get("tremolo")
    if trem:
        x = modal.tremolo(x, trem["depth"], trem["hz"], rate)
    fade = min(len(x), int(0.02 * rate))
    for k in range(fade):
        x[len(x) - fade + k] *= 0.5 * (1.0 + math.cos(math.pi * (k + 1) / fade))
    return spectral.Built(spectral.to_int16(x),
                          int(round(rate * spectral.C5_HZ / f_base)), None,
                          f"modal {low}-{high}")


register_sampler("modal", modal_sampler)
register_family(ModalFamily())
