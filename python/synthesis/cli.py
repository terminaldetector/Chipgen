"""cli.py — `python3 python/forge.py`, the synthesis layer's command line.

    axes                      the dials, what they mean, what each chip can do
    archetypes                named starting points
    compile CHIP              one DNA -> real hardware parameters, measured
    survey                    every archetype on every chip, intended vs heard
    sweep CHIP AXIS           one dial from 0 to 1, everything else held
    run SCENARIO.json         search: seed -> mutate -> probe -> validate ->
                              score -> retain, into a bank
    check SCENARIO.json       read a scenario and say what it would do
    template CHIP             a scenario to start from
    mix CHIP A B [C...]       blend instruments (or a recipe tree) into one
    place SCORE.trk           where do these instruments sit in this score?
    show BANK / export BANK   read a bank / write the engine's own bank files
    play BANK [NAME]          render notes to audio, to listen
    nes-inst NAME             write an NES instrument by hand, as macros
    director-prompt SCENARIO  the question a model would be asked

Everything runs offline. A model is optional and is only ever asked which
way to push the dials (`run --director llm`) and which finalist to keep.
"""

import argparse
import json
import os
import sys
import time

from . import archetypes, bank as bank_mod, evolve, mixing
from .backends import get_backend, backends as backend_names
from .dna import AXES, AXIS_DOC, DNA, DNAError
from .genome import Genome, slug

_NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


class CliError(Exception):
    pass


# --------------------------------------------------------------------------
# Small parsers
# --------------------------------------------------------------------------
def parse_register(text: str):
    """'A3', 'C#4', 'A-3' -> ('A', 3)."""
    t = text.strip().replace("-", "")
    if len(t) < 2 or t[0].upper() not in _NOTE or not t[-1].isdigit():
        raise CliError(f"{text!r} is not a note like A3 or C#4")
    name = t[0].upper() + ("#" if "#" in t[1:-1] else "")
    return name, int(t[-1])


def parse_dna(text: str, base: DNA = None) -> DNA:
    """'brightness=0.7,attack=0.1' -> DNA (axes not named keep `base`)."""
    values = {}
    for part in filter(None, (text or "").split(",")):
        if "=" not in part:
            raise CliError(f"{part!r}: write axis=value, e.g. brightness=0.7")
        key, value = part.split("=", 1)
        values[key.strip()] = float(value)
    if not values:
        return base or DNA()
    merged = (base or DNA()).to_dict(6)
    from .dna import _canonical
    for key, value in values.items():
        axis = _canonical(key)
        if axis is None:
            raise CliError(f"{key!r} is not an axis; the axes are "
                           f"{', '.join(AXES)}")
        merged[axis] = value
    return DNA.from_dict(merged)


def _bar(value, width=10):
    if value is None:
        return " " * width
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "#" * filled + "." * (width - filled)


def _say(args, text):
    if not getattr(args, "quiet", False):
        print(text, file=sys.stderr)


def _write_audio(path, mono, rate, peak=0.8):
    import audio
    import wavio
    top = max((abs(v) for v in mono), default=0.0)
    gain = peak / top if top > 0 else 1.0
    buf = audio.from_floats([v * gain for v in mono], 1)
    if int(rate) != 44100:            # the chips run at their own clocks
        buf = audio.resample(buf, rate, 44100)
        rate = 44100
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    if path.lower().endswith(".mp3"):
        import mp3
        mp3.encode(buf, int(rate), path)
    else:
        wavio.write(path, buf, int(rate))
    return path


def _render_note(backend, compiled, register, seconds=1.6, velocity=127):
    note, octave = register
    mono, rate = backend.render(compiled, note, octave, velocity, seconds, 0.6)
    return mono, rate


def _table(measured, intended=None, axes=AXES, supports=None):
    lines = []
    for axis in axes:
        if supports is not None and axis not in supports:
            continue
        got = measured.get(axis)
        want = getattr(intended, axis) if intended is not None else None
        got_s = "  -  " if got is None else f"{got:5.2f}"
        want_s = "" if want is None else f"  asked {want:4.2f}"
        flag = ""
        if got is not None and want is not None and abs(got - want) > 0.15:
            flag = f"   <-- {got - want:+.2f}"
        lines.append(f"  {axis:13s} {got_s} {_bar(got)}{want_s}{flag}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_axes(args):
    for axis in AXES:
        low, high, how = AXIS_DOC[axis]
        print(f"{axis}\n    0 = {low}\n    1 = {high}\n    measured: {how}")
    print()
    for name in backend_names():
        b = get_backend(name)
        missing = [a for a in AXES if a not in b.supports]
        print(f"{name:7s} {b.chip}\n    cannot express: "
              f"{', '.join(missing) or 'nothing'};  channels {b.channels}")
    return 0


def cmd_archetypes(args):
    print(f"{'name':12s} {'reg':4s} {'roles':16s} "
          + " ".join(f"{a[:5]:>5s}" for a in AXES) + "  what")
    for name in archetypes.names():
        dna, reg, roles, doc = archetypes.get(name)
        print(f"{name:12s} {reg[0]}{reg[1]:<3d} {','.join(roles):16s} "
              + " ".join(f"{getattr(dna, a):5.2f}" for a in AXES)
              + f"  {doc}")
    return 0


def _genome_from_args(args, backend):
    if args.archetype:
        dna, reg, _, _ = archetypes.get(args.archetype)
        label = args.archetype
    else:
        dna, reg, label = DNA(), backend.default_register, "dna"
    dna = parse_dna(args.dna, dna) if args.dna else dna
    if args.register:
        reg = parse_register(args.register)
    name = slug(args.name or f"{label}_{backend.name}")
    style = {}
    for item in filter(None, (args.style or "").split(",")):
        k, v = item.split("=", 1)
        style[k] = int(v) if v.isdigit() else v
    return Genome(backend.name, dna, style=style, name=name, register=reg,
                  lineage=[f"cli:{label}"])


def cmd_compile(args):
    backend = get_backend(args.chip)
    genome = _genome_from_args(args, backend)
    full = args.full or genome.dna.detune > 0.05 or genome.dna.motion > 0.05
    started = time.time()
    g, compiled, result = evolve.realise(
        backend, genome, probes=1 if args.open_loop else args.probes,
        full=full)
    print(f"{backend.chip}: {g.name}  ({time.time() - started:.1f} s, "
          f"{'open loop' if args.open_loop else 'corrected against the render'})")
    print(f"register {g.register[0]}{g.register[1]}   {g.dna.describe(0.22)}")
    print("how it was built:")
    for line in compiled.notes:
        print(f"  - {line}")
    print("asked vs heard:")
    print(_table(result.axes, g.dna, supports=backend.supports))
    for issue in result.issues:
        print(f"  [{issue.level}] {issue.message}"
              + (f"  -> {issue.fix}" if issue.fix else ""))
    if args.hardware:
        print(json.dumps(backend.to_dict(compiled), indent=1))
    if args.out:
        out = bank_mod.Bank.load(args.out) if os.path.exists(args.out) \
            else bank_mod.Bank(slug(os.path.basename(args.out).split(".")[0]))
        entry = bank_mod.Entry(
            g.name, backend.name, g, compiled, dict(result.axes),
            0.0, args.role or "", g.dna.describe(0.22),
            [str(i) for i in result.issues])
        out.add(entry)
        out.save(args.out)
        print(f"wrote {args.out} ({len(out.entries)} instruments)")
    if args.wav:
        mono, rate = _render_note(backend, compiled, g.register)
        print(f"wrote {_write_audio(args.wav, mono, rate)}")
    return 0 if result.ok else 1


def cmd_survey(args):
    chips = [args.chip] if args.chip else backend_names()
    names = [args.archetype] if args.archetype else archetypes.names()
    worst = 0.0
    for chip in chips:
        backend = get_backend(chip)
        print(f"\n== {backend.chip}")
        print(f"{'archetype':12s} " + " ".join(f"{a[:5]:>5s}" for a in AXES
                                                if a in backend.supports)
              + "   (asked -> heard; blank = not measured)")
        for name in names:
            dna, reg, _, _ = archetypes.get(name)
            if (name.startswith("noise_") and chip == "ym2612") or \
                    (name.startswith("noise_") and chip == "opl2"):
                continue                      # no noise generator to build on
            g = Genome(chip, dna, name=f"{name}_{chip}", register=reg)
            full = dna.detune > 0.05 or dna.motion > 0.05
            try:
                g, compiled, result = evolve.realise(backend, g, probes=3,
                                                     full=full)
            except Exception as error:         # a survey keeps going
                print(f"{name:12s} failed: {error}")
                continue
            cells = []
            for axis in AXES:
                if axis not in backend.supports:
                    continue
                got = result.axes.get(axis)
                if got is None:
                    cells.append("     ")
                    continue
                err = got - getattr(dna, axis)
                worst = max(worst, abs(err))
                cells.append(f"{got:5.2f}" if abs(err) <= 0.15
                             else f"{got:4.2f}!")
            print(f"{name:12s} " + " ".join(cells)
                  + ("" if result.ok else "  INVALID: "
                     + ", ".join(i.code for i in result.issues)))
    print("\n'!' marks a dial more than 0.15 from what was asked: the chip "
          "cannot reach it from that structure (see docs/SYNTHESIS.md).")
    return 0


def cmd_sweep(args):
    from . import probe as probe_mod
    backend = get_backend(args.chip)
    base = parse_dna(args.base, DNA(brightness=.5, attack=.1, body=.6,
                                    decay=.5, metallicity=.2, bass_weight=.4,
                                    articulation=.5))
    reg = parse_register(args.register) if args.register \
        else backend.default_register
    pinned = backend.pin(Genome(backend.name, base, name="sweep",
                                register=reg))
    cols = [a for a in AXES if a in backend.supports]
    print(f"{backend.chip}, {args.axis} swept, open loop (no correction), "
          f"structure {pinned.style}")
    print(f"{'':14s}" + " ".join(f"{a[:5]:>5s}" for a in cols))
    for step in range(args.steps):
        v = step / (args.steps - 1) if args.steps > 1 else 0.0
        g = pinned.clone(dna=base.with_(**{args.axis: v}))
        compiled = backend.compile(g)
        result = probe_mod.probe(backend, compiled, g, full=args.full or
                                 args.axis in ("detune", "motion"))
        cells = " ".join(("%5.2f" % result.axes[a]) if result.axes.get(a)
                         is not None else "    -" for a in cols)
        print(f"{args.axis[:8]:>8s}={v:4.2f}  {cells}")
    return 0


def _load_director(args, purpose=""):
    spec = args.director
    if not spec:
        return None
    from . import director as director_mod
    endpoint = None
    if spec == "llm":
        import llm
        try:
            endpoint = llm.Endpoint(args.endpoint, args.model)
        except ValueError as error:
            raise CliError(str(error)) from None
    try:
        return director_mod.make_director(spec, endpoint, purpose)
    except (ValueError, OSError) as error:
        raise CliError(str(error)) from None


def cmd_run(args):
    scenario = bank_mod.load_scenario(args.scenario)
    director = _load_director(args, scenario.get("note", ""))
    started = time.time()

    def progress(row):
        _say(args, f"  generation {row['generation']}: made {row['made']}, "
                   f"kept {row['kept']}, best {row['best']}, "
                   f"{row['probes']} renders, {row['seconds']} s")
    _say(args, f"running {scenario.get('name', args.scenario)} on "
               f"{scenario['backend']}")
    result = bank_mod.run_scenario(scenario, progress=progress,
                                   director=director, seconds=args.seconds)
    print(result.table())
    out = args.out or os.path.splitext(args.scenario)[0] + ".bank.json"
    result.save(out)
    print(f"\nwrote {out} ({len(result.entries)} instruments, "
          f"{time.time() - started:.1f} s)")
    if director is not None and getattr(director, "log", None):
        log = out.replace(".json", ".director.json")
        with open(log, "w", encoding="utf-8") as handle:
            json.dump({"calls": director.calls, "errors": director.errors,
                       "notes": director.notes, "log": director.log},
                      handle, indent=1)
        print(f"wrote {log} (what the model was asked and said)")
    if args.export:
        for chip, path in result.export_runtime(args.export).items():
            print(f"wrote {path}  ({chip})")
    print(f"use it: python3 python/chipgen.py SCORE.trk --forge-bank {out}")
    if args.wav:
        for entry in result.entries:
            backend = get_backend(entry.backend)
            mono, rate = _render_note(backend, entry.compiled,
                                      entry.genome.register)
            path = os.path.join(args.wav, f"{entry.name}.wav")
            _write_audio(path, mono, rate)
        print(f"wrote {len(result.entries)} audition files to {args.wav}")
    return 0


def cmd_check(args):
    scenario = bank_mod.load_scenario(args.scenario)
    backend = get_backend(scenario["backend"])
    register = tuple(scenario["register"]) if scenario.get("register") else None
    seeds = bank_mod.seeds_of(backend, scenario, register)
    objective = bank_mod.objective_of(backend, scenario, seeds)
    print(f"{scenario.get('name', args.scenario)}: {backend.chip}")
    print(f"  seeds: {len(seeds)}  "
          + ", ".join(s.lineage[0] if s.lineage else "?" for s in seeds))
    print(f"  objective axes: {', '.join(objective.targeted())}")
    unreachable = [a for a in objective.targeted() if a not in backend.supports]
    if unreachable:
        print(f"  NOTE: {backend.name} cannot express "
              f"{', '.join(unreachable)}; those are not scored")
    gens = int(scenario.get("generations", 6))
    kids = int(scenario.get("offspring", 8))
    print(f"  budget: {gens} generations x {kids} offspring "
          f"(~{gens * kids + len(seeds)} candidates), keep "
          f"{scenario.get('keep', 6)}")
    for i, recipe in enumerate(scenario.get("mix", []), 1):
        print(f"  mix {i}: {json.dumps(recipe)[:100]}")
    return 0


def cmd_template(args):
    backend = get_backend(args.chip)
    role = args.role
    seeds = archetypes.for_role(role)[:2] if role else \
        (["square_lead"] if backend.name == "nes" else ["saw_lead"])
    seeds = [s for s in seeds if not (s.startswith("noise_")
                                      and backend.name != "nes")] or ["saw_lead"]
    template = {
        "format": bank_mod.SCENARIO_FORMAT,
        "name": f"{args.chip}_{role or 'lead'}",
        "note": "what this is for, in a sentence (shown to a model that "
                "ranks the finalists)",
        "backend": backend.name,
        "role": role or "lead",
        "seeds": seeds,
        "objective": {"target": {"brightness": 0.7, "attack": 0.05,
                                 "metallicity": 0.3},
                      "box": {}, "weights": {}, "ignore": []},
        "register": list(backend.default_register),
        "generations": 6, "offspring": 8, "keep": 4, "archive": 12,
        "novelty": 0.10, "seed": 1,
        "mix": [{"mix": [{"from": f"archetype:{seeds[0]}"},
                         {"from": f"archetype:{seeds[-1]}"}]}]
        if len(seeds) > 1 else [],
    }
    print(json.dumps(template, indent=1))
    return 0


def _bank_for(args, chip=None):
    if getattr(args, "bank", None):
        return bank_mod.Bank.load(args.bank)
    return bank_mod.Bank("scratch")


def cmd_mix(args):
    backend = get_backend(args.chip)
    source = _bank_for(args)
    if args.recipe:
        with open(args.recipe, encoding="utf-8") as handle:
            recipe = json.load(handle)
        entry = mixing.run_recipe(backend, recipe, source,
                                  slug(args.name or "mix"), args.role or "")
    else:
        if len(args.parts) < 2:
            raise CliError("name at least two instruments to mix (bank "
                           "entries, built-in patches or archetype:NAME), or "
                           "pass --recipe FILE")
        weights = [float(w) for w in args.weights.split(",")] \
            if args.weights else None
        entry = mixing.mix(backend, args.parts, weights, args.mode, source,
                           slug(args.name or "mix"))
    print(f"{entry.name}: {entry.character}")
    print(_table(entry.measured, None, supports=backend.supports))
    for issue in entry.issues:
        print(f"  {issue}")
    if args.out:
        out = bank_mod.Bank.load(args.out) if os.path.exists(args.out) \
            else bank_mod.Bank(slug(os.path.basename(args.out).split(".")[0]))
        out.add(entry)
        out.save(args.out)
        print(f"wrote {args.out}")
    if args.wav:
        mono, rate = _render_note(backend, entry.compiled,
                                  entry.genome.register)
        print(f"wrote {_write_audio(args.wav, mono, rate)}")
    return 0


_VOICE_CHIP = (("fm", "ym2612"), ("opl", "opl2"), ("nes", "nes"),
               ("pulse", "nes"), ("tri", "nes"))


def _chip_of_voice(voice: str, nes_score: bool = False):
    for prefix, chip in _VOICE_CHIP:
        if voice.startswith(prefix):
            return chip
    if voice == "noise" and nes_score:
        return "nes"
    return None


def cmd_place(args):
    import score_model
    from . import arrangement
    source = _bank_for(args)
    score = score_model.load(args.score)
    assignment, genomes = {}, {}
    for spec in args.assign or []:
        if "=" not in spec:
            raise CliError(f"--assign wants VOICE=INSTRUMENT, got {spec!r}")
        voice, name = spec.split("=", 1)
        chip = _chip_of_voice(voice, score_model.is_nes(score))
        if chip is None:
            raise CliError(f"{voice}: the forge covers fm*, opl* and nes* "
                           f"voices (psg and noise are not part of it)")
        backend = get_backend(chip)
        entry = next((e for e in source.entries
                      if e.name == name and e.backend == chip), None)
        if entry is not None:
            assignment[voice] = (chip, entry.compiled)
            genomes[voice] = entry.genome
        elif name.startswith("archetype:"):
            dna, reg, _, _ = archetypes.get(name.split(":", 1)[1])
            g = Genome(chip, dna, name=slug(name), register=reg)
            g, compiled, _ = evolve.realise(backend, g, probes=3)
            assignment[voice] = (chip, compiled)
            genomes[voice] = g
        else:
            try:
                compiled = mixing.builtin_compiled(backend, name)
            except KeyError:
                raise CliError(f"{name!r} for {voice}: not in the bank, not "
                               f"a built-in {chip} patch and not "
                               f"archetype:NAME") from None
            assignment[voice] = (chip, compiled)
    if not assignment:
        raise CliError("nothing to place: pass --assign fm0=NAME ...")
    roles = {}
    for spec in args.roles or []:
        voice, role = spec.split("=", 1)
        roles[voice] = role
    arr = arrangement.Arrangement(score, assignment, roles=roles)
    report = arr.analyse()
    print(report.text())
    suggestions = arr.suggest(report)
    if suggestions:
        print("\nwhat would help:")
        for s in suggestions:
            extra = []
            if "velocity_scale" in s:
                extra.append(f"level x{s['velocity_scale']:.2f}")
            if s.get("dna"):
                extra.append("dna " + ", ".join(
                    f"{a}{d:+.2f}" for a, d in s["dna"].items()))
            if s.get("octave"):
                extra.append(f"octave {s['octave']:+d}")
            print(f"  {s['part']}: {s['say']}  [{'; '.join(extra)}]")
    if args.fit:
        before = report.cost
        best, applied = arrangement.fit(arr, genomes, rounds=args.rounds,
                                        verbose=print)
        print(f"\ncost {before:.2f} -> {best.cost:.2f} after "
              f"{len(applied)} changes\n{best.text()}")
        if args.out:
            out = bank_mod.Bank.load(args.out) if os.path.exists(args.out) \
                else bank_mod.Bank("placed")
            for voice, (chip, compiled) in arr.assignment.items():
                if voice not in genomes:
                    continue
                g = genomes[voice]
                clone = get_backend(chip).from_dict(
                    get_backend(chip).to_dict(compiled))
                clone.name = slug(f"{compiled.name}_{voice}")
                entry = bank_mod.Entry(
                    clone.name, chip, g.clone(name=clone.name), clone, {}, 0.0,
                    roles.get(voice, ""), g.dna.describe(0.22))
                out.add(entry)
                scale = arr.scales.get(voice)
                if scale and abs(scale - 1.0) > 0.02:
                    print(f"  {voice}: play it at x{scale:.2f} "
                          f"(`vol {voice} {int(round(127 * scale))}`)")
            out.save(args.out)
            print(f"wrote {args.out}")
    return 0


def cmd_show(args):
    b = bank_mod.Bank.load(args.bank)
    print(f"{b.name}: {len(b.entries)} instruments")
    print(b.table())
    for e in b.entries:
        extra = []
        if e.backend == "nes":
            extra.append(e.compiled.patch.channel)
        layers = e.compiled.style.get("layers") if e.backend != "nes" \
            else e.compiled.patch.layers
        if layers:
            extra.append(f"detune layer {len(layers)}")
        if e.compiled.setup:
            extra.append("setup " + json.dumps(e.compiled.setup))
        print(f"  {e.name}: {e.character}" + (f" [{', '.join(extra)}]"
                                              if extra else ""))
    return 0


def cmd_export(args):
    b = bank_mod.Bank.load(args.bank)
    for chip, path in b.export_runtime(args.dir, args.stem).items():
        flag = {"ym2612": "--bank", "opl2": "--opl-bank", "nes": "(nes bank)"}
        print(f"wrote {path}  ({chip}: {flag[chip]})")
    return 0


def cmd_play(args):
    b = bank_mod.Bank.load(args.bank)
    entries = [b.get(args.name)] if args.name else b.entries
    reg = parse_register(args.note) if args.note else None
    if args.join:
        # every instrument in turn, one file: a way to hear a whole bank
        # in under a minute. Each plays its own register unless --note.
        import audio
        joined, rate_out = [], 44100
        for entry in entries:
            backend = get_backend(entry.backend)
            mono, rate = _render_note(backend, entry.compiled,
                                      reg or entry.genome.register,
                                      args.seconds)
            buf = audio.from_floats(list(mono), 1)
            if int(rate) != rate_out:
                buf = audio.resample(buf, rate, rate_out)
            samples = [float(v) for v in
                       (buf.data if hasattr(buf, "data") else buf)]
            top = max((abs(v) for v in samples), default=0.0) or 1.0
            joined += [v * 0.8 / top for v in samples]
            joined += [0.0] * int(0.15 * rate_out)
        print(f"wrote {_write_audio(args.join, joined, rate_out)}  "
              f"({len(entries)} instruments)")
        return 0
    for entry in entries:
        backend = get_backend(entry.backend)
        mono, rate = _render_note(backend, entry.compiled,
                                  reg or entry.genome.register, args.seconds)
        ext = "mp3" if args.mp3 else "wav"
        path = args.out if (args.out and args.name) else os.path.join(
            args.out or "output/forge", f"{entry.name}.{ext}")
        print(f"wrote {_write_audio(path, mono, rate)}")
    return 0


def cmd_nes_inst(args):
    """Write an NES instrument as the macros it is, by hand."""
    from . import nes_driver as N
    from .backends.base import Compiled
    backend = get_backend("nes")
    try:
        inst = N.NESInstrument(
            name=slug(args.name), channel=args.channel,
            volume=N.parse_macro(args.volume, "volume", 0, 15, whole=True),
            duty=N.parse_macro(args.duty, "duty", 0, 3, whole=True),
            pitch=N.parse_macro(args.pitch, "pitch (cents)"),
            arp=N.parse_macro(args.arp, "arp (semitones)", whole=True),
            period=N.parse_macro(args.period, "period", 0, 15, whole=True),
            mode=1 if args.metallic else 0)
    except ValueError as error:
        raise CliError(str(error)) from None
    if args.layer:
        try:
            cents, volume = args.layer.split(":")
            inst.layers.append({"cents": float(cents), "volume": float(volume),
                                "duty_shift": 0})
        except ValueError:
            raise CliError("--layer wants CENTS:VOLUME, e.g. 6:0.4 (a second "
                           "pulse six cents sharp at 40% of the volume)") \
                from None
        if args.channel != "pulse":
            raise CliError("only a pulse instrument can have a layer: it "
                           "plays on the other pulse channel")
    compiled = Compiled("nes", inst.name, inst, {},
                        {"gate": 1.0, "retrigger": True,
                         "channel": args.channel,
                         "pitched": args.channel != "noise"},
                        ["written by hand as macros"])
    reg = parse_register(args.register) if args.register \
        else backend.default_register
    from . import mixing
    genome = mixing.lift(backend, compiled, reg, inst.name)
    from . import probe as probe_mod
    result = probe_mod.probe(backend, compiled, genome, full=True)
    print(f"{inst.name}: {args.channel} instrument, "
          f"{len(inst.volume.values)} volume frames")
    print("heard:")
    print(_table(result.axes, None, supports=backend.supports))
    for issue in result.issues:
        print(f"  [{issue.level}] {issue.message}"
              + (f"  -> {issue.fix}" if issue.fix else ""))
    if args.out:
        out = bank_mod.Bank.load(args.out) if os.path.exists(args.out) \
            else bank_mod.Bank(slug(os.path.basename(args.out).split(".")[0]))
        out.add(bank_mod.Entry(inst.name, "nes", genome, compiled,
                               dict(result.axes), 0.0, args.role or "",
                               args.about or "written by hand",
                               [str(i) for i in result.issues]))
        out.save(args.out)
        print(f"wrote {args.out}; use it with --forge-bank and "
              f"`inst nes{ {'pulse': 0, 'triangle': 2, 'noise': 3}[args.channel] } {inst.name}`")
    if args.wav:
        mono, rate = _render_note(backend, compiled, reg)
        print(f"wrote {_write_audio(args.wav, mono, rate)}")
    return 0 if result.ok else 1


_CHIP_PREFIX = {"ym2612": "fm", "opl2": "opl", "nes": "nes"}


def cmd_starter(args):
    """Realise every archetype on every chip into the shipped starter bank."""
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "banks", "starter.bank.json")
    result = bank_mod.Bank("starter")
    for chip in backend_names():
        backend = get_backend(chip)
        for name in archetypes.names():
            if name.startswith("noise_") and chip != "nes":
                continue
            dna, reg, roles, doc = archetypes.get(name)
            label = f"{_CHIP_PREFIX[chip]}_{name}"
            g = Genome(chip, dna, name=label, register=reg,
                       lineage=[f"archetype:{name}"])
            full = dna.detune > 0.05 or dna.motion > 0.05
            g, compiled, res = evolve.realise(backend, g, probes=3, full=full)
            compiled.name = label
            entry = bank_mod.Entry(
                label, chip, g, compiled, dict(res.axes), 0.0,
                roles[0], doc, [str(i) for i in res.issues])
            result.add(entry)
            _say(args, f"  {label}")
    result.scenario = None
    result.save(out)
    print(f"wrote {out} ({len(result.entries)} instruments)")
    return 0


def cmd_director_prompt(args):
    from . import director as director_mod
    scenario = bank_mod.load_scenario(args.scenario)
    backend = get_backend(scenario["backend"])
    register = tuple(scenario["register"]) if scenario.get("register") else None
    seeds = bank_mod.seeds_of(backend, scenario, register)
    objective = bank_mod.objective_of(backend, scenario, seeds)
    forge = evolve.Forge(backend, objective, seed=int(scenario.get("seed", 0)),
                         register=register)
    forge.seed(seeds)
    for message in director_mod.directions_prompt(forge.summary()):
        print(f"--- {message['role']} ---\n{message['content']}\n")
    print("Save the model's JSON reply as reply.json, then:\n"
          f"  python3 python/forge.py run {args.scenario} "
          "--director file:reply.json")
    return 0


# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="forge.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    add("axes", cmd_axes, "the dials and what each chip can reach")
    add("archetypes", cmd_archetypes, "named starting points")

    sp = add("compile", cmd_compile, "one DNA -> hardware parameters")
    sp.add_argument("chip", choices=backend_names())
    sp.add_argument("--archetype", help="start from a named archetype")
    sp.add_argument("--dna", help="axis=value,... (over the archetype)")
    sp.add_argument("--register", help="where it will be played, e.g. A3")
    sp.add_argument("--style", help="structural choices, e.g. alg=4,ratios=bell")
    sp.add_argument("--name")
    sp.add_argument("--role")
    sp.add_argument("--probes", type=int, default=3,
                    help="render-and-correct rounds (default 3)")
    sp.add_argument("--open-loop", action="store_true",
                    help="compile once, no correction")
    sp.add_argument("--full", action="store_true",
                    help="long probe (reads detune and motion)")
    sp.add_argument("--hardware", action="store_true",
                    help="print the registers/macros as JSON")
    sp.add_argument("--out", metavar="BANK.json", help="add it to a bank")
    sp.add_argument("--wav", metavar="FILE", help="render a note to listen")

    sp = add("survey", cmd_survey, "every archetype on every chip")
    sp.add_argument("--chip", choices=backend_names())
    sp.add_argument("--archetype", choices=archetypes.names())

    sp = add("sweep", cmd_sweep, "one dial from 0 to 1")
    sp.add_argument("chip", choices=backend_names())
    sp.add_argument("axis", choices=AXES)
    sp.add_argument("--steps", type=int, default=6)
    sp.add_argument("--base", help="axis=value,... for the other dials")
    sp.add_argument("--register")
    sp.add_argument("--full", action="store_true")

    sp = add("run", cmd_run, "search from a scenario file")
    sp.add_argument("scenario")
    sp.add_argument("--out", help="bank to write (default SCENARIO.bank.json)")
    sp.add_argument("--export", metavar="DIR",
                    help="also write the engine's --bank/--opl-bank files")
    sp.add_argument("--wav", metavar="DIR", help="audition files for the keepers")
    sp.add_argument("--seconds", type=float, help="stop after this long")
    sp.add_argument("--director", metavar="WHO",
                    help="none | heuristic | file:reply.json | llm")
    sp.add_argument("--endpoint", help="chat server URL for --director llm")
    sp.add_argument("--model", help="model name for --director llm")
    sp.add_argument("--quiet", action="store_true")

    sp = add("check", cmd_check, "read a scenario without running it")
    sp.add_argument("scenario")

    sp = add("template", cmd_template, "print a scenario to start from")
    sp.add_argument("chip", choices=backend_names())
    sp.add_argument("--role")

    sp = add("mix", cmd_mix, "blend instruments")
    sp.add_argument("chip", choices=backend_names())
    sp.add_argument("parts", nargs="*",
                    help="bank entries, built-in patches or archetype:NAME")
    sp.add_argument("--bank", help="where the names are looked up first")
    sp.add_argument("--weights", help="e.g. 2,1")
    sp.add_argument("--mode", choices=("auto", "dna", "morph"), default="auto")
    sp.add_argument("--recipe", help="a recipe tree as JSON")
    sp.add_argument("--name")
    sp.add_argument("--role")
    sp.add_argument("--out", metavar="BANK.json")
    sp.add_argument("--wav", metavar="FILE")

    sp = add("place", cmd_place, "where instruments sit in a score")
    sp.add_argument("score")
    sp.add_argument("--assign", action="append", metavar="VOICE=NAME",
                    help="e.g. fm0=fm_punch_bass (bank entry, built-in or "
                         "archetype:NAME); repeatable")
    sp.add_argument("--bank")
    sp.add_argument("--roles", action="append", metavar="VOICE=ROLE",
                    help="say bass/lead/harmony/arp/pad/counter when the "
                         "score's shape does not")
    sp.add_argument("--fit", action="store_true",
                    help="retune the bank's instruments to the arrangement")
    sp.add_argument("--rounds", type=int, default=4)
    sp.add_argument("--out", metavar="BANK.json",
                    help="with --fit: write the retuned instruments")

    sp = add("show", cmd_show, "read a bank")
    sp.add_argument("bank")

    sp = add("export", cmd_export, "write --bank / --opl-bank files")
    sp.add_argument("bank")
    sp.add_argument("dir")
    sp.add_argument("--stem")

    sp = add("play", cmd_play, "render notes to listen to")
    sp.add_argument("bank")
    sp.add_argument("name", nargs="?")
    sp.add_argument("--note", help="e.g. A3 (default: its own register)")
    sp.add_argument("--seconds", type=float, default=1.6)
    sp.add_argument("--out")
    sp.add_argument("--mp3", action="store_true")
    sp.add_argument("--join", metavar="FILE",
                    help="all of them one after another in one file")

    sp = add("nes-inst", cmd_nes_inst,
             "write an NES instrument by hand, as macros")
    sp.add_argument("name")
    sp.add_argument("--channel", choices=("pulse", "triangle", "noise"),
                    default="pulse")
    sp.add_argument("--volume", default="15", help='frames 0-15, e.g. '
                    '"15 14 | 12 / 8 4 0" (| loop start, / release start)')
    sp.add_argument("--duty", default="2", help="frames 0-3 (12.5/25/50/75%%)")
    sp.add_argument("--pitch", default="0", help="cents per frame")
    sp.add_argument("--arp", default="0", help="semitones per frame")
    sp.add_argument("--period", default="6", help="noise period 0-15 per frame")
    sp.add_argument("--metallic", action="store_true",
                    help="noise: the short, tonal shift register")
    sp.add_argument("--layer", metavar="CENTS:VOLUME",
                    help="a second pulse, e.g. 6:0.4")
    sp.add_argument("--register", help="where it will be played, e.g. A4")
    sp.add_argument("--role")
    sp.add_argument("--about", help="a sentence for the bank")
    sp.add_argument("--out", metavar="BANK.json")
    sp.add_argument("--wav", metavar="FILE")

    sp = add("starter", cmd_starter,
             "rebuild the shipped starter bank (one per archetype per chip)")
    sp.add_argument("--out")
    sp.add_argument("--quiet", action="store_true")

    sp = add("director-prompt", cmd_director_prompt,
             "the steering question a model would be asked")
    sp.add_argument("scenario")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except (CliError, bank_mod.BankError, DNAError, mixing.MixError,
            KeyError, ValueError, OSError) as error:
        message = error.args[0] if isinstance(error, KeyError) and error.args \
            else str(error)
        print(f"forge: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
