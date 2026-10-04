"""fam_tone.py — the tone family: a loop built from a spectrum.

Leads, basses, pads, organs, keys, brass, stabs. The sample is a loop
(`spectral.py`) with an intro: an attack ramp, a bright transient, and for a
fast-falling note the fall itself, so that a 40 ms decay is 40 ms whatever
tempo the song runs at. Everything slower than that is the instrument's
volume envelope, in seconds here and ticks only when a module takes it.

What each dial does to the compiled instrument:

    brightness   the roll-off of the partials, solved so the centroid sits at
                 16**brightness times the fundamental (tonelib.design)
    bass_weight  the fundamental's weight against the next three partials
    metallicity  first inharmonic stretch of the partials (a loop of 16
                 cycles, so a partial can sit between two multiples), then
                 an alternating roughness of the upper ones
    detune       unison voices a bin or two apart, beating at 44100/N Hz a
                 bin; the side voices' level sets how deep
    motion       vibrato (Bessel sidebands of the right depth in cents),
                 tremolo, or both, on a bin of the loop
    noise        random-phase lines in every bin, tilted
    attack       a ramp in the sample under 60 ms, the volume envelope above
    body, decay  the fall to the body level, baked under 0.12 s time
                 constant, an envelope above
    articulation the release segment of the volume envelope
    punch        a brighter copy of the first milliseconds
"""

import math

from . import descriptors as D, spectral, tonelib as T
from .family import Compiled, Family, register_family
from .genome import Genome
from .patch import Patch, REFERENCE_TICK, register_sampler

#: partials a tone is designed with, before the band limit takes some away
LONG_FRAMES = 32768
LONGER_FRAMES = 65536
#: an attack faster than this is a ramp in the sample; slower, the envelope
BAKE_ATTACK_S = 0.060
#: a fall with a time constant under this is baked into the sample
BAKE_DECAY_S = 0.120
#: A punchy note is brighter for the stretch the analysis reads as its
#: attack (from 8 ms to ten periods or 46 ms, whichever is longer), then it
#: falls away over this long. Shorter and the reading sees mostly the body.
PUNCH_FALL_S = 0.020
PUNCH_MAX_S = 0.140
#: The transient is designed this much brighter than the punch asks for:
#: the analysis window that reads it also sees the note after it, which
#: dilutes it (measured).
PUNCH_BOOST = 1.3
#: A side voice of this level beats this deep (the Goertzel track reads the
#: beat at roughly this share of its true depth; measured, see survey).
DETUNE_GAIN = 1.8
INHARMONIC_GRID = 16


def register_hz(register):
    from .probe import note_of, note_hz
    return note_hz(note_of(*register))


class ToneFamily(Family):
    name = "tone"
    doc = ("a loop built from a spectrum, with unison, vibrato, noise and an "
           "intro: leads, basses, pads, organs, keys, brass")
    default_register = ("A", 3)
    genes = {"shape": ["saw", "square", "pulse", "organ", "reed", "vowel"],
             "unison": [3, 2],
             "motion_kind": ["vibrato", "tremolo", "both"]}
    pitched = True

    # -- structure ------------------------------------------------------------
    def pin(self, genome: Genome) -> Genome:
        style = dict(genome.style)
        d = genome.dna
        if style.get("shape") in (None, "auto"):
            style["shape"] = ("square" if d.brightness < 0.45
                              and d.metallicity < 0.3 and d.detune < 0.3
                              and d.bass_weight < 0.5 else "saw")
        style.setdefault("unison", 3)
        style.setdefault("motion_kind", "vibrato")
        return genome.clone(style=style)

    # -- compile ----------------------------------------------------------------
    def compile(self, genome: Genome) -> Compiled:
        g = self.clamp_genome(self.pin(genome))
        d = g.effective()
        st = g.style
        f0 = register_hz(g.register)
        notes = []
        count = int(max(8, min(T.MAX_PARTIALS, D.TOP_HZ / f0)))

        # -- the body spectrum -------------------------------------------------
        # A note that sustains is read mostly off its body (brightness is
        # 0.4 body, 0.6 attack); one that dies away is read off its first
        # 50 ms only. So a sustained note is designed body-first, with the
        # transient a brighter copy of it; a percussive one transient-first,
        # with the body a darker copy that keeps the same fundamental.
        sustained = d.body >= 0.4
        m_in = min(1.0, 2.0 * d.metallicity)           # inharmonic first
        m_ro = max(0.0, 2.0 * d.metallicity - 1.0)
        inharm = D.METAL_INHARM_FULL * m_in if m_in > 0.12 else 0.0
        rough_t = D.METAL_ROUGH_FULL * m_ro if m_ro > 0.05 else 0.0
        extra = 3.0 * d.punch                          # octaves, attack over body
        ratio = 16.0 ** d.brightness
        bright = None
        if sustained or d.punch <= 0.02:
            ln_ratio = d.brightness * T.LN16
            if d.punch > 0.02:
                ln_ratio -= 0.6 * extra * math.log(2.0)
            partials, p, g1, stretch, rough = T.design(
                st["shape"], count, max(1.0, math.exp(ln_ratio)),
                T.bass_ratio(d.bass_weight), inharm, rough_t)
            if d.punch > 0.02:
                bright = T.transient(partials, st["shape"], count,
                                     PUNCH_BOOST * extra)
        else:
            bright, p, g1, stretch, rough = T.design(
                st["shape"], count, ratio, T.bass_ratio(d.bass_weight),
                inharm, rough_t)
            dark_ratio = max(1.0, ratio / 2.0 ** extra)
            partials, _, _, _, _ = T.design(
                st["shape"], count, dark_ratio,
                T.bass_ratio(d.bass_weight) * 4.0, inharm, rough_t)
            a1_t = max((a for r, a in bright if r < 1.5), default=1.0)
            a1_b = max((a for r, a in partials if r < 1.5), default=1.0)
            partials = [(r, a * a1_t / a1_b) for r, a in partials]
        notes.append(f"{st['shape']} partials rolling off as h^({-p:.2f}), "
                     f"fundamental x{g1:.2f}"
                     + (f", stretched {stretch:.3f}" if stretch else "")
                     + (f", rough {rough:.2f}" if rough else ""))
        params = {"partials": [[round(r, 5), round(a, 6)] for r, a in partials],
                  "grid": INHARMONIC_GRID if stretch > 0.0 else 1,
                  "phases": "sine", "seed": 7}

        # -- long loops: unison, vibrato, noise -----------------------------------
        k_low = 1
        hv = T.harmonic_vector(partials, 6)
        top = max(hv) or 1.0
        for i in range(6):
            if hv[i] >= 0.4 * top:
                k_low = i + 1
                break
        long_loop = False
        frames = LONG_FRAMES
        voices = [[0, 1.0]]
        if d.detune > 0.02:
            long_loop = True
            depth = (D.MOVEMENT_FLOOR + D.DETUNE_FULL * d.detune) * DETUNE_GAIN
            if st["unison"] == 3:
                rho = min(0.6, depth / 2.0)
                voices = [[0, 1.0], [1, round(rho, 4)], [-1, round(rho, 4)]]
            else:
                rho = min(0.9, depth)
                voices = [[0, 1.0], [1, round(rho, 4)]]
            if k_low >= 3:
                frames = LONGER_FRAMES
            notes.append(f"unison {len(voices)} voices, side level "
                         f"{voices[-1][1]:.2f}, beat "
                         f"{k_low * spectral.SAMPLE_RATE / frames:.2f} Hz on "
                         f"partial {k_low}")
        am = pm = 0.0
        if d.motion > 0.02:
            long_loop = True
            kind = st["motion_kind"]
            share = 0.5 if kind == "both" else 1.0
            if kind in ("vibrato", "both"):
                std = 1.0 + D.MOTION_PM_FULL * d.motion * share
                pm = 1.4142 * std
            if kind in ("tremolo", "both"):
                am = D.MOVEMENT_FLOOR + D.MOTION_AM_FULL * d.motion * share
            notes.append(f"{kind}: {pm:.1f} cents peak, {am:.2f} depth")
        noise = 0.0
        if d.noise > 0.02:
            long_loop = True
            noise = 10.0 ** (-3.3 + 2.2 * d.noise)
            notes.append(f"noise lines at {20 * math.log10(noise):.0f} dB "
                         f"against a partial")
        if long_loop:
            params.update({
                "long": True, "frames": frames,
                "voices": voices, "am": round(am, 4), "pm": round(pm, 3),
                "lfo_bins": 4 if frames == LONG_FRAMES else 8,
                "noise": round(noise, 5), "noise_tilt": -3.0,
                "phases": "random" if noise else "sine"})

        # -- the envelope -----------------------------------------------------------
        attack_s = T.attack_seconds(d.attack)
        ramp = attack_s / 0.8                     # a linear ramp reads as 0.8 of itself
        sustain = T.sustain_ratio(d.body)
        if d.decay < 0.95 and sustain > 0.8:
            sustain = 0.8                         # a fall that can be seen
        tau = None if d.decay >= 0.99 or sustain >= 0.97 \
            else T.decay_seconds(d.decay)
        t20 = T.release_seconds(d.articulation)
        bake_attack = ramp <= BAKE_ATTACK_S
        bake_decay = tau is not None and tau <= BAKE_DECAY_S
        params["attack_s"] = round(ramp, 5) if bake_attack and ramp > 0.0015 \
            else 0.0
        if bake_decay:
            params["bake"] = {"tau": round(tau, 5), "level": round(sustain, 4)}
        if bright is not None:
            window = 0.008 + max(2048 / 44100.0, 10.0 / f0)
            hold = min(PUNCH_MAX_S - PUNCH_FALL_S, max(0.035, 0.95 * window))
            params["punch"] = {"partials": [[round(r, 4), round(a, 6)]
                                            for r, a in bright],
                               "hold": round(hold, 4),
                               "fall": PUNCH_FALL_S}
            notes.append(f"transient: {extra:.1f} octaves brighter for "
                         f"{(hold + PUNCH_FALL_S) * 1000:.0f} ms")
        amp, sus, release_to = _amp_envelope(ramp, bake_attack, tau, sustain,
                                             bake_decay, t20)
        patch = Patch(self.name, params, amp=amp, amp_sustain=sus)
        patch.inst["nna"] = "fade" if d.articulation >= 0.55 else "cut"
        if patch.inst["nna"] == "fade":
            patch.inst["fade"] = int(max(1, min(256, round(
                2048.0 * REFERENCE_TICK / (2.0 * t20)))))
        patch.inst["gain"] = 128
        o = g.register[1]
        patch.bands = (max(1, 12 * (o - 2) + 1), min(120, 12 * (o + 3)), 12)
        notes.append(f"attack {attack_s * 1000:.0f} ms"
                     + (" in the sample" if bake_attack else " in the envelope")
                     + (f", falls with tau {tau * 1000:.0f} ms to "
                        f"{20 * math.log10(sustain):.0f} dB"
                        + (" (in the sample)" if bake_decay else "")
                        if tau else f", holds at {20 * math.log10(sustain):.0f} dB")
                     + f", release -20 dB in {t20 * 1000:.0f} ms")
        return Compiled(self.name, g.name or "tone", patch, {},
                        {"pitched": True, "release_expected": True,
                         "long": long_loop}, notes)

    def plain(self, compiled: Compiled):
        params = compiled.patch.params
        if len(params.get("voices", [0])) <= 1 and not params.get("am") \
                and not params.get("pm"):
            return None
        out = Compiled(compiled.family, compiled.name,
                       compiled.patch.clone(), dict(compiled.setup),
                       dict(compiled.style), list(compiled.notes))
        p = out.patch.params
        p["voices"] = [[0, 1.0]]
        p["am"] = p["pm"] = 0.0
        return out


def _amp_envelope(ramp, bake_attack, tau, sustain, bake_decay, t20):
    """The volume envelope in seconds: [[t, level]], the sustain pair, and
    the time the release begins."""
    nodes = []
    if bake_decay:
        nodes.append([0.0, 1.0])
        sus = [0, 0]
        t_hold = 0.0
        level = 1.0
    else:
        if bake_attack or ramp <= 0.0:
            nodes.append([0.0, 1.0])
            t = 0.0
        else:
            nodes.append([0.0, 0.0])
            t = ramp
            nodes.append([t, 1.0])
        if tau is not None:
            for x in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
                nodes.append([round(t + x * tau, 5),
                              round(sustain + (1.0 - sustain)
                                    * math.exp(-x), 4)])
            t = t + 4.0 * tau
            level = nodes[-1][1]
        else:
            level = 1.0
        sus = [len(nodes) - 1] * 2
        t_hold = nodes[-1][0]
    # release: S * 10^(-x) at t20 * x, then silence
    for x in (0.25, 0.5, 1.0, 1.6, 2.4):
        nodes.append([round(t_hold + t20 * x, 5),
                      round(level * 10.0 ** (-x), 4)])
    nodes.append([round(t_hold + t20 * 3.2, 5), 0.0])
    # node times must increase
    for i in range(1, len(nodes)):
        if nodes[i][0] <= nodes[i - 1][0]:
            nodes[i][0] = round(nodes[i - 1][0] + 0.001, 5)
    return nodes, sus, t_hold


# -- the sample ---------------------------------------------------------------------
def chord_cycles(notes, nominal: int, reach: int = 3) -> int:
    """The number of cycles of the fundamental a long loop should hold so
    that a chord's notes fall closest to the bins they must sit on: each
    note is a line at `cycles x 2^(n/12)`, and a line can only be on a whole
    bin. Tries the nominal count and `reach` either side (the pitch of the
    key is exact whatever the count: `c5speed` follows it); returns the
    one with the least cents off in its worst note."""
    best, best_cost = nominal, None
    for m in sorted(range(max(1, nominal - reach), nominal + reach + 1),
                    key=lambda k: abs(k - nominal)):
        worst = 0.0
        for n in notes:
            exact = m * 2.0 ** (n / 12.0)
            bin_ = max(1, int(round(exact)))
            worst = max(worst, abs(1200.0 * math.log2(bin_ / exact)))
        if best_cost is None or worst < best_cost - 1e-9:
            best, best_cost = m, worst
    return best


def stereo_bins(cycles: int, cents: float) -> int:
    """Whole bins either side of the pitch that make a pair of loops
    `cents` apart on each side, or at least one: a loop can only move in
    steps of 1/cycles of its pitch."""
    return max(1, int(round(cycles * (2.0 ** (abs(cents) / 1200.0) - 1.0))))


def tone_sampler(params: dict, low: int, high: int):
    """One sample serving notes `low`..`high`.

    `chord` (semitones above the key) puts the spectrum on several roots at
    once; `stereo` (cents) builds two loops of the same recipe, one a little
    flat and one a little sharp, as the left and right of a stereo sample.
    Both need a loop that is fine enough to put a line where it belongs, so
    they use the long loop; the pitch of the key stays exact."""
    base = (low + high) // 2
    f_base = spectral.hz_of_note(base)
    note_high = spectral.hz_of_note(high)
    partials = [(r, a) for r, a in params["partials"]]
    punch = params.get("punch")
    notes = None
    if params.get("chord"):
        from . import splice
        notes = splice.chord_notes(params["chord"])
        weight = 1.0 / math.sqrt(len(notes))
        partials = [(r * 2.0 ** (n / 12.0), a * weight)
                    for n in notes for r, a in partials]
        if punch:
            punch = dict(punch, partials=[
                [r * 2.0 ** (n / 12.0), a * weight]
                for n in notes for r, a in punch["partials"]])
    cents = float(params.get("stereo") or 0.0)
    shifts = (0,)
    if params.get("long") or notes or cents:
        frames = int(params.get("frames", LONG_FRAMES))
        if notes or cents:
            frames = max(frames, LONGER_FRAMES)
        cycles = max(1, int(round(f_base * frames / spectral.SAMPLE_RATE)))
        if notes:
            cycles = chord_cycles(notes, cycles)
        if cents:
            j = stereo_bins(cycles, cents)
            shifts = (-j, j)
    else:
        # frames per cycle: the smallest power of two that holds the highest
        # partial the band limit allows (two frames per cycle of it, as the
        # plain multisamples do), so a stored second is about a played one
        cycles = int(params.get("grid", 1))
        ratio_cap = spectral.BAND_LIMIT_HZ / note_high
        top_ratio = min(max(r for r, _ in partials), ratio_cap)
        per_cycle = 32
        while per_cycle < spectral.MIN_FRAMES_PER_CYCLE * top_ratio \
                and per_cycle < 4096:
            per_cycle *= 2
        frames = per_cycle * cycles
    # stored frames per second of playback at the base pitch: where a
    # recipe says "60 ms" it is this many frames
    rate = f_base * frames / float(cycles)
    ramp = int(round(params.get("attack_s", 0.0) * rate))
    bake = params.get("bake")
    hold_frames = int(round(punch["hold"] * rate)) if punch else 0
    punch_frames = hold_frames + int(round(punch["fall"] * rate)) \
        if punch else 0
    tau_frames = 0
    level = 1.0
    if bake:
        tau_frames = max(1, int(round(bake["tau"] * rate)))
        level = bake["level"]
        intro_len = int(4.6 * tau_frames)
    else:
        intro_len = max(ramp, punch_frames)
    intro_len = min(intro_len, 6 * frames) if intro_len > frames else intro_len
    one_shot = bool(bake) and level < 0.012
    # a modulation rate is counted in bins, and a longer loop has narrower
    # bins: the same rate in hertz takes proportionally more of them
    lfo_bins = int(round(params.get("lfo_bins", 0) * frames
                         / float(params.get("frames", frames))))
    rendered = []
    for shift in shifts:
        kwargs = dict(frames=frames, cycles=cycles + shift,
                      phases=params.get("phases", "sine"),
                      voices=[tuple(v) for v in params.get("voices",
                                                           [[0, 1.0]])],
                      am=params.get("am", 0.0), pm=params.get("pm", 0.0),
                      lfo_bins=lfo_bins,
                      noise=params.get("noise", 0.0),
                      noise_tilt=params.get("noise_tilt", -3.0),
                      seed=params.get("seed", 7), note_high=note_high)
        spec = spectral.LoopSpec(partials, **kwargs)
        x = spectral.loop_signal(spec)
        top = max(abs(v) for v in x) or 1.0
        scale = 1.0 / top
        loop = [v * scale for v in x]
        boost = None
        if punch:
            bright = spectral.LoopSpec([tuple(x) for x in punch["partials"]],
                                       **kwargs)
            boost = [v * scale for v in spectral.loop_signal(bright)]
            # the transient can be louder than the body; keep it in range
            peak_b = max(abs(v) for v in boost)
            if peak_b > 1.6:
                boost = [v * 1.6 / peak_b for v in boost]
        data = []
        if intro_len > 0:
            n = len(loop)
            end_decay = math.exp(-intro_len / float(tau_frames)) \
                if bake else 0.0
            for t in range(intro_len):
                v = loop[(t - intro_len) % n]
                if boost is not None and t < punch_frames:
                    # full strength through the analysis window, then a fall
                    g = 1.0 if t < hold_frames else \
                        1.0 - (t - hold_frames) / float(punch_frames
                                                        - hold_frames)
                    v += g * (boost[(t - intro_len) % n] - v)
                if bake:
                    e = math.exp(-t / float(tau_frames))
                    v *= level + (1.0 - level) * (e - end_decay) \
                        / (1.0 - end_decay)
                if t < ramp:
                    v *= t / float(ramp)
                data.append(v)
        if one_shot:
            spectral_fade(data)
        else:
            gain = level if bake else 1.0
            data.extend(v * gain for v in loop)
        rendered.append(data)
    looped = None if one_shot else (intro_len, intro_len + frames)
    speed = spectral.tuned_c5speed(frames, cycles)
    if len(rendered) == 2:
        # one scale for both, or the louder loop would move the image
        top = max(max(abs(v) for v in d) for d in rendered) or 1.0
        pcm = [spectral.to_int16([v / top for v in d]) for d in rendered]
        return spectral.Built(pcm[0], speed, looped, f"tone {low}-{high}",
                              right=pcm[1])
    return spectral.Built(spectral.to_int16(rendered[0]), speed, looped,
                          f"tone {low}-{high}")


def spectral_fade(x, count: int = 441):
    n = min(len(x), count)
    for k in range(n):
        x[len(x) - n + k] *= 0.5 * (1.0 + math.cos(math.pi * (k + 1) / n))


register_sampler("tone", tone_sampler, handles=("chord", "stereo"))
register_family(ToneFamily())
