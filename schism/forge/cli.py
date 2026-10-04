"""cli.py — `python3 -m schism forge`, the synthesis layer's command line.

    axes                      the dials and what each one means
    families                  the ways a sound is made, and their kinds
    archetypes                named starting points
    compile NAME              one DNA -> an instrument, asked against heard
    survey                    every archetype, asked against heard
    sweep FAMILY AXIS         one dial from 0 to 1, everything else held
    run SCENARIO.json         search: seed -> mutate -> probe -> validate ->
                              score -> retain, into a bank
    check SCENARIO.json       read a scenario and say what it would do
    template FAMILY           a scenario to start from
    mix A B [C...]            blend instruments (or a recipe tree) into one
    layer A@click B@body      stack instruments: PCM spliced by role, not averaged
    kit NAME --style S        a drum kit whose drums leave room for each other
    show BANK                 read a bank
    play BANK [NAME...]       render the instruments to audio, to listen
    analyse SCORE             where do the channels of this module sit?
    lift 'wave=saw oct=3-5'   measure an `inst` line and print its DNA
    starter                   rebuild the bank that ships with the forge
    director-prompt SCENARIO  the question a model would be asked

Everything runs offline. A model is optional and is only ever asked which
way to push the dials (`run --director llm`) and which finalist to keep.
"""

import argparse
import json
import os
import sys
import time

from . import archetypes, bank as bank_mod, evolve, fam_layer, mixing
from .dna import AXES, AXIS_DOC, DNA, DNAError
from .family import families, get_family
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
    merged = (base or DNA()).to_dict(6)
    from .dna import _canonical
    for key, value in values.items():
        axis = _canonical(key)
        if axis is None:
            raise CliError(f"{key!r} is not a DNA axis; the axes are "
                           f"{', '.join(AXES)}")
        merged[axis] = value
    try:
        return DNA.from_dict(merged)
    except DNAError as error:
        raise CliError(str(error)) from None


def _bar(value, width=10):
    if value is None:
        return " " * width
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "#" * filled + "." * (width - filled)


def _say(args, text):
    if not getattr(args, "quiet", False):
        print(text, file=sys.stderr)


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


def _audition(items, path):
    from . import audition
    seconds, peak = audition.render(items, path)
    return seconds, peak


def _items_of(entries):
    return [(e.name, e.compiled, e.genome.register,
             e.role or (archetypes.get(e.genome.lineage[0].split(":")[1]).roles[0]
                        if e.genome.lineage and e.genome.lineage[0].startswith(
                            "archetype:") and e.genome.lineage[0].split(":")[1]
                        in archetypes.ARCHETYPES else "lead"))
            for e in entries]


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_axes(args):
    for axis in AXES:
        low, high, how = AXIS_DOC[axis]
        print(f"{axis}\n    0 = {low}\n    1 = {high}\n    measured: {how}")
    print()
    for name in families():
        f = get_family(name)
        missing = [a for a in AXES if a not in f.supports]
        print(f"{name:7s} {f.doc}\n    cannot express: "
              f"{', '.join(missing) or 'nothing'}")
    return 0


def cmd_families(args):
    for name in families():
        f = get_family(name)
        print(f"{name:7s} {f.doc}")
        kinds = getattr(f, "kinds", None)
        if kinds:
            for kind, (_, register, doc) in sorted(kinds.items()):
                print(f"          kind {kind:8s} tuned {register[0]}"
                      f"{register[1]}  {doc}")
        elif f.genes:
            for gene, options in sorted(f.genes.items()):
                print(f"          {gene}: {', '.join(str(o) for o in options)}")
    return 0


def cmd_archetypes(args):
    print(f"{'name':12s} {'family':6s} {'reg':4s} {'roles':16s} "
          + " ".join(f"{a[:5]:>5s}" for a in AXES) + "  what")
    for name in archetypes.names():
        a = archetypes.get(name)
        if args.family and a.family != args.family:
            continue
        print(f"{name:12s} {a.family:6s} {a.register[0]}{a.register[1]:<3d} "
              f"{','.join(a.roles):16s} "
              + " ".join(f"{getattr(a.dna, x):5.2f}" for x in AXES)
              + f"  {a.doc}")
    return 0


def _genome_from_args(args):
    if args.archetype:
        try:
            a = archetypes.get(args.archetype)
        except KeyError as error:
            raise CliError(str(error.args[0])) from None
        family, dna, reg, style = a.family, a.dna, a.register, dict(a.style)
        label = args.archetype
    else:
        family = args.family or "tone"
        dna, reg, label, style = DNA(), get_family(family).default_register, \
            "dna", {}
    if args.family and args.archetype and args.family != family:
        raise CliError(f"{args.archetype} is a {family} sound, not "
                       f"{args.family}")
    dna = parse_dna(args.dna, dna) if args.dna else dna
    if args.register:
        reg = parse_register(args.register)
    for item in filter(None, (args.style or "").split(",")):
        k, v = item.split("=", 1)
        style[k] = int(v) if v.isdigit() else v
    if getattr(args, "kind", None):
        style["kind"] = args.kind
    name = slug(args.name or f"{label}_{family}")
    return Genome(family, dna, style=style, name=name, register=reg,
                  lineage=[f"cli:{label}"])


def cmd_compile(args):
    genome = _genome_from_args(args)
    family = get_family(genome.family)
    full = args.full or genome.dna.detune > 0.05 or genome.dna.motion > 0.05
    started = time.time()
    g, compiled, result = evolve.realise(
        family, genome, probes=1 if args.open_loop else args.probes,
        full=full)
    print(f"{family.name}: {g.name}  ({time.time() - started:.1f} s, "
          f"{'open loop' if args.open_loop else 'corrected against the render'})")
    print(f"register {g.register[0]}{g.register[1]}   {g.dna.describe(0.22)}")
    print("how it was built:")
    for line in compiled.notes:
        print(f"  - {line}")
    print("asked vs heard:")
    print(_table(result.axes, g.dna, supports=family.supports))
    for issue in result.issues:
        print(f"  [{issue.level}] {issue.message}"
              + (f"  -> {issue.fix}" if issue.fix else ""))
    if args.patch:
        print(json.dumps(family.to_dict(compiled), indent=1))
    if args.out:
        out = bank_mod.Bank.load(args.out) if os.path.exists(args.out) \
            else bank_mod.Bank(slug(os.path.basename(args.out).split(".")[0]))
        entry = bank_mod.Entry(g.name, family.name, g, compiled,
                               dict(result.axes), 0.0, args.role or "",
                               g.dna.describe(0.22),
                               [str(i) for i in result.issues])
        out.add(entry)
        out.save(args.out)
        print(f"wrote {args.out} ({len(out.entries)} instruments)")
    if args.mp3 or args.wav:
        role = args.role or (archetypes.get(args.archetype).roles[0]
                             if args.archetype else "lead")
        path = args.mp3 or args.wav
        seconds, peak = _audition([(g.name, compiled, g.register, role)], path)
        print(f"wrote {path} ({seconds:.1f} s, peak {peak:.2f})")
    return 0 if result.ok else 1


def cmd_survey(args):
    names = [args.archetype] if args.archetype else sorted(
        archetypes.names(), key=lambda n: (archetypes.get(n).family, n))
    worst = 0.0
    shown = None
    for name in names:
        a = archetypes.get(name)
        if args.family and a.family != args.family:
            continue
        family = get_family(a.family)
        if shown != a.family:
            shown = a.family
            print(f"\n== {family.name}: {family.doc}")
            print(f"{'archetype':12s} " + " ".join(f"{x[:5]:>5s}" for x in AXES
                                                    if x in family.supports)
                  + "   (asked -> heard; blank = not measured)")
        g = Genome(a.family, a.dna, style=dict(a.style), name=name,
                   register=a.register)
        full = a.dna.detune > 0.05 or a.dna.motion > 0.05
        try:
            g, compiled, result = evolve.realise(family, g, probes=3,
                                                 full=full)
        except Exception as error:             # a survey keeps going
            print(f"{name:12s} failed: {error}")
            continue
        cells = []
        for axis in AXES:
            if axis not in family.supports:
                continue
            got = result.axes.get(axis)
            if got is None:
                cells.append("     ")
                continue
            err = got - getattr(a.dna, axis)
            worst = max(worst, abs(err))
            cells.append(f"{got:5.2f}" if abs(err) <= 0.15 else f"{got:4.2f}!")
        print(f"{name:12s} " + " ".join(cells)
              + ("" if result.ok else "  INVALID: "
                 + ", ".join(i.code for i in result.issues)))
    print("\n'!' marks a dial more than 0.15 from what was asked: the family "
          "cannot reach it from that structure (see docs/FORGE.md).")
    return 0


def cmd_sweep(args):
    from . import probe as probe_mod
    family = get_family(args.family)
    base = parse_dna(args.base, DNA(brightness=.5, attack=.1, body=.6,
                                    decay=.5, metallicity=.2, bass_weight=.4,
                                    articulation=.5))
    reg = parse_register(args.register) if args.register \
        else family.default_register
    style = {"kind": args.kind} if args.kind else {}
    pinned = family.pin(Genome(family.name, base, style=style, name="sweep",
                               register=reg))
    cols = [a for a in AXES if a in family.supports]
    print(f"{family.name}, {args.axis} swept, open loop (no correction), "
          f"structure {pinned.style}")
    print(f"{'':14s}" + " ".join(f"{a[:5]:>5s}" for a in cols))
    for step in range(args.steps):
        v = step / (args.steps - 1) if args.steps > 1 else 0.0
        g = pinned.clone(dna=base.with_(**{args.axis: v}))
        compiled = family.compile(g)
        result = probe_mod.probe(compiled, g, full=args.full or
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
        from . import llm
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
               f"{scenario['family']}")
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
    print(f"use it: put `bank {os.path.basename(out)}` in a score, then "
          f"`inst 1 forge={result.entries[0].name}`")
    if args.mp3:
        seconds, peak = _audition(_items_of(result.entries), args.mp3)
        print(f"wrote {args.mp3} ({seconds:.1f} s, peak {peak:.2f})")
    return 0


def cmd_check(args):
    scenario = bank_mod.load_scenario(args.scenario)
    family = get_family(scenario["family"])
    register = tuple(scenario["register"]) if scenario.get("register") else None
    seeds = bank_mod.seeds_of(family, scenario, register)
    objective = bank_mod.objective_of(scenario, seeds)
    print(f"{scenario.get('name', args.scenario)}: {family.name}")
    print(f"  seeds: {len(seeds)}  "
          + ", ".join(s.lineage[0] if s.lineage else "?" for s in seeds))
    print(f"  objective axes: {', '.join(objective.targeted())}")
    unreachable = [a for a in objective.targeted() if a not in family.supports]
    if unreachable:
        print(f"  NOTE: {family.name} cannot express "
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
    family = get_family(args.family)
    seeds = [n for n in archetypes.for_role(args.role)
             if archetypes.get(n).family == family.name][:2] \
        if args.role else archetypes.for_family(family.name)[:2]
    seeds = seeds or archetypes.for_family(family.name)[:1] or ["saw_lead"]
    template = {
        "format": bank_mod.SCENARIO_FORMAT,
        "name": f"{family.name}_{args.role or 'new'}",
        "note": "what this is for, in a sentence (shown to a model that "
                "ranks the finalists)",
        "family": family.name,
        "role": args.role or archetypes.get(seeds[0]).roles[0],
        "seeds": seeds,
        "objective": {"target": {"brightness": 0.7, "attack": 0.05,
                                 "metallicity": 0.3},
                      "box": {}, "weights": {}, "ignore": []},
        "register": list(family.default_register),
        "generations": 6, "offspring": 8, "keep": 4, "archive": 12,
        "novelty": 0.10, "seed": 1,
        "mix": [{"mix": [{"from": f"archetype:{seeds[0]}"},
                         {"from": f"archetype:{seeds[-1]}"}]}]
        if len(seeds) > 1 else [],
    }
    print(json.dumps(template, indent=1))
    return 0


def cmd_mix(args):
    source = bank_mod.Bank.load(args.bank) if args.bank else bank_mod.Bank("scratch")
    if args.recipe:
        with open(args.recipe, encoding="utf-8") as handle:
            recipe = json.load(handle)
        entry = mixing.run_recipe(recipe, source, slug(args.name or "mix"),
                                  args.role or "", args.family)
    else:
        if len(args.parts) < 2:
            raise CliError("name at least two instruments to mix (bank "
                           "entries, archetype:NAME or recipe:wave=...), or "
                           "pass --recipe FILE")
        weights = [float(w) for w in args.weights.split(",")] \
            if args.weights else None
        entry = mixing.mix(args.parts, weights, args.mode, source,
                           slug(args.name or "mix"), args.family)
    family = get_family(entry.family)
    print(f"{entry.name}: {entry.character}")
    print(_table(entry.measured, None, supports=family.supports))
    for issue in entry.issues:
        print(f"  {issue}")
    if args.out:
        out = bank_mod.Bank.load(args.out) if os.path.exists(args.out) \
            else bank_mod.Bank(slug(os.path.basename(args.out).split(".")[0]))
        out.add(entry)
        out.save(args.out)
        print(f"wrote {args.out}")
    if args.mp3:
        seconds, peak = _audition(_items_of([entry]), args.mp3)
        print(f"wrote {args.mp3} ({seconds:.1f} s, peak {peak:.2f})")
    return 0


def cmd_layer(args):
    """A stack of instruments: the first moments of one, the body of another
    (`clap@click snare@body`). Parts without an @role take --roles in order,
    or, with neither, the roles the sounds suggest."""
    source = bank_mod.Bank.load(args.bank) if args.bank \
        else bank_mod.Bank("scratch")
    tokens = [t for arg in args.parts for t in arg.split("+") if t.strip()]
    if len(tokens) < 2:
        raise CliError("name at least two parts, e.g. `layer clap@click "
                       "snare@body` (a part is NAME, NAME@ROLE or "
                       "NAME@ROLE/ms=40/db=-3; the roles are "
                       + ", ".join(fam_layer.ROLES) + ")")
    roles = [r.strip() for r in args.roles.split(",")] if args.roles else []
    if roles and len(roles) != len(tokens):
        raise CliError(f"--roles gives {len(roles)} roles for "
                       f"{len(tokens)} parts; give one for each, in order")
    parts = []
    for i, token in enumerate(tokens):
        name, role, options = fam_layer.parse_part(token)
        if roles:
            if role:
                raise CliError(f"{token!r} names its role, and --roles "
                               f"names one too")
            role = roles[i]
        part = {"from": name}
        if role:
            part["role"] = role
        part.update(options)
        parts.append(part)
    recipe = {"layer": parts}
    if args.chord:
        recipe["chord"] = args.chord
        recipe["root"] = args.root
    elif args.root:
        raise CliError("--root moves a chord: it needs --chord")
    if args.stereo:
        recipe["stereo"] = args.stereo
    if args.rev:
        recipe["rev"] = True
    entry = mixing.run_recipe(recipe, source, slug(args.name or "layer"),
                              args.role or "")
    family = get_family(entry.family)
    print(f"{entry.name}: {entry.character}")
    print(_table(entry.measured, None, supports=family.supports))
    for line in entry.compiled.notes:
        print(f"  - {line}")
    for issue in entry.issues:
        print(f"  {issue}")
    if args.out:
        out = bank_mod.Bank.load(args.out) if os.path.exists(args.out) \
            else bank_mod.Bank(slug(os.path.basename(args.out).split(".")[0]))
        out.add(entry)
        out.save(args.out)
        print(f"wrote {args.out}; in a score:\n"
              f"  bank {os.path.basename(args.out)}\n"
              f"  inst 1 name={entry.name[:25]} forge={entry.name}")
    if args.mp3:
        seconds, peak = _audition(_items_of([entry]), args.mp3)
        print(f"wrote {args.mp3} ({seconds:.1f} s, peak {peak:.2f})")
    return 0


def cmd_show(args):
    bank = bank_mod.Bank.load(args.bank)
    print(f"{bank.name}: {len(bank.entries)} instruments"
          + (f", {len(bank.kits)} kits" if bank.kits else ""))
    print(bank.table())
    for kit in bank.kits:
        print(f"kit {kit['name']} ({kit.get('style', '')}): "
              + ", ".join(f"{k}={n}" for k, n in kit["keys"]))
    if args.name:
        e = bank.get(args.name)
        print(f"\n{e.name}: {e.character}")
        for line in e.compiled.notes:
            print(f"  - {line}")
        if args.patch:
            print(json.dumps(get_family(e.family).to_dict(e.compiled),
                             indent=1))
    return 0


def cmd_play(args):
    bank = bank_mod.Bank.load(args.bank)
    entries = [bank.get(n) for n in args.names] if args.names else bank.entries
    out = args.out or os.path.splitext(args.bank)[0] + ".mp3"
    seconds, peak = _audition(_items_of(entries), out)
    print(f"wrote {out} ({len(entries)} instruments, {seconds:.0f} s, "
          f"peak {peak:.2f})")
    return 0


def cmd_kit(args):
    from . import kits
    started = time.time()
    roles = args.roles.split(",") if args.roles else None
    bank = kits.forge_kit(args.name, args.style, args.seed, roles,
                          args.generations, args.offspring,
                          progress=lambda text: _say(args, "  " + text))
    print(bank.table())
    out = args.out or f"{slug(args.name)}.bank.json"
    if os.path.exists(out) and args.append:
        existing = bank_mod.Bank.load(out)
        for e in bank.entries:
            existing.add(e)
        existing.kits.extend(bank.kits)
        bank = existing
    bank.save(out)
    print(f"\nwrote {out} ({time.time() - started:.1f} s); in a score:\n"
          f"  bank {os.path.basename(out)}\n"
          f"  inst 1 name=Kit forge=kit:{slug(args.name)}")
    if args.mp3:
        kit = bank.kit(slug(args.name))
        members = [bank.get(n) for _, n in kit["keys"]]
        seconds, peak = _audition(_items_of(members), args.mp3)
        print(f"wrote {args.mp3} ({seconds:.0f} s, peak {peak:.2f})")
    return 0


def _load_module(path):
    from .. import it_read, notation
    if path.lower().endswith(".it"):
        return it_read.read_file(path), None
    try:
        module, report = notation.compile_file(path)
    except notation.NotationError as error:
        raise CliError(str(error)) from None
    return module, report


def cmd_analyse(args):
    from . import arrangement
    from .. import it_write
    module, _ = _load_module(args.score)
    roles = {}
    for item in filter(None, (args.roles or "").split(",")):
        voice, _, role = item.partition("=")
        roles[voice] = role
    arr = arrangement.Arrangement(module, roles)
    started = time.time()
    report = arr.analyse(measure=args.measure)
    if args.fit:
        before = report.cost
        report, applied = arrangement.fit(
            arr, rounds=args.rounds, measure=args.measure,
            verbose=lambda text: _say(args, "  " + text))
        print(f"fit: cost {before:.2f} -> {report.cost:.2f}, "
              f"{len(applied)} changes")
        for s in applied:
            print(f"  - {s['say']}")
    if args.json:
        print(json.dumps(report.to_dict(), indent=1))
    else:
        print(report.text())
    if args.fit and args.out:
        blob = it_write.build(module)
        with open(args.out, "wb") as handle:
            handle.write(blob)
        print(f"wrote {args.out} ({len(blob) / 1024:.0f} KB)")
    _say(args, f"({time.time() - started:.1f} s)")
    return 0


def cmd_lift(args):
    g = mixing.lift_recipe(args.recipe)
    print(f"{g.family}, register {g.register[0]}{g.register[1]}: "
          f"{g.dna.describe(0.22)}")
    for axis in AXES:
        print(f"  {axis:13s} {getattr(g.dna, axis):5.2f} "
              f"{_bar(getattr(g.dna, axis))}")
    return 0


def cmd_starter(args):
    from . import kits as kits_mod
    from . import notation_hook
    started = time.time()
    bank = bank_mod.Bank("starter")
    for name in archetypes.names():
        a = archetypes.get(name)
        family = get_family(a.family)
        g = Genome(a.family, a.dna, style=dict(a.style), name=name,
                   register=a.register, lineage=[f"archetype:{name}"])
        full = a.dna.detune > 0.05 or a.dna.motion > 0.05
        g, compiled, result = evolve.realise(family, g, probes=4, full=full)
        from . import score as score_mod
        obj, _ = score_mod.objective_fit(
            score_mod.Objective(a.dna), result.axes, family.supports)
        entry = bank_mod.Entry(name, a.family, g, compiled, dict(result.axes),
                               obj, a.roles[0], a.doc,
                               [str(i) for i in result.issues])
        bank.add(entry)
        _say(args, f"  {name:12s} fit {obj:.2f}")
    for style in args.kits.split(","):
        sub = kits_mod.forge_kit(f"{style}_kit", style, seed=1,
                                 generations=2, offspring=5)
        for e in sub.entries:
            bank.add(e)
        bank.kits.extend(sub.kits)
        _say(args, f"  kit {style}")
    out = args.out or notation_hook.starter_path()
    bank.save(out)
    print(f"wrote {out} ({len(bank.entries)} instruments, {len(bank.kits)} "
          f"kits, {os.path.getsize(out) / 1024:.0f} KB, "
          f"{time.time() - started:.0f} s)")
    return 0


def cmd_director_prompt(args):
    from . import director as director_mod
    scenario = bank_mod.load_scenario(args.scenario)
    family = get_family(scenario["family"])
    register = tuple(scenario["register"]) if scenario.get("register") else None
    seeds = bank_mod.seeds_of(family, scenario, register)
    objective = bank_mod.objective_of(scenario, seeds)
    forge = evolve.Forge(family, objective, seed=int(scenario.get("seed", 0)),
                         register=register)
    forge.seed(seeds)
    for message in director_mod.directions_prompt(forge.summary()):
        print(f"--- {message['role']} ---\n{message['content']}\n")
    print("Save the model's JSON reply as reply.json, then:\n"
          f"  python3 -m schism forge run {args.scenario} "
          "--director file:reply.json")
    return 0


# --------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        prog="python3 -m schism forge",
        description="Generate instruments, drums and effects for Impulse "
                    "Tracker modules: measured, searched, mixed, offline.")
    parser.add_argument("-q", "--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, help_, fn):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(run=fn)
        return sp

    add("axes", "the dials and what each means", cmd_axes)
    add("families", "the ways a sound is made", cmd_families)
    sp = add("archetypes", "named starting points", cmd_archetypes)
    sp.add_argument("--family")

    sp = add("compile", "one DNA -> an instrument, measured", cmd_compile)
    sp.add_argument("archetype", nargs="?")
    sp.add_argument("--family")
    sp.add_argument("--kind", help="drum or fx kind (kick, hat, riser...)")
    sp.add_argument("--dna", help="axis=value,... to move")
    sp.add_argument("--register")
    sp.add_argument("--style", help="gene=value,... (shape=square)")
    sp.add_argument("--name")
    sp.add_argument("--role")
    sp.add_argument("--probes", type=int, default=4)
    sp.add_argument("--open-loop", action="store_true")
    sp.add_argument("--full", action="store_true")
    sp.add_argument("--patch", action="store_true", help="print the patch")
    sp.add_argument("--out", help="append to this bank")
    sp.add_argument("--mp3")
    sp.add_argument("--wav")

    sp = add("survey", "every archetype, asked against heard", cmd_survey)
    sp.add_argument("--family")
    sp.add_argument("--archetype")

    sp = add("sweep", "one dial from 0 to 1", cmd_sweep)
    sp.add_argument("family")
    sp.add_argument("axis", choices=AXES)
    sp.add_argument("--base")
    sp.add_argument("--register")
    sp.add_argument("--kind")
    sp.add_argument("--steps", type=int, default=6)
    sp.add_argument("--full", action="store_true")

    sp = add("run", "run a scenario into a bank", cmd_run)
    sp.add_argument("scenario")
    sp.add_argument("--out")
    sp.add_argument("--seconds", type=float)
    sp.add_argument("--director", help="none|heuristic|file:PATH|llm")
    sp.add_argument("--endpoint")
    sp.add_argument("--model")
    sp.add_argument("--mp3", help="audition the result")

    sp = add("check", "what a scenario would do", cmd_check)
    sp.add_argument("scenario")
    sp = add("template", "a scenario to start from", cmd_template)
    sp.add_argument("family")
    sp.add_argument("--role")

    sp = add("mix", "blend instruments into a new one", cmd_mix)
    sp.add_argument("parts", nargs="*")
    sp.add_argument("--weights")
    sp.add_argument("--mode", choices=("auto", "dna", "morph"), default="auto")
    sp.add_argument("--bank")
    sp.add_argument("--recipe")
    sp.add_argument("--family")
    sp.add_argument("--name")
    sp.add_argument("--role")
    sp.add_argument("--out")
    sp.add_argument("--mp3")

    sp = add("layer", "stack instruments by role: PCM spliced, not averaged",
             cmd_layer)
    sp.add_argument("parts", nargs="+",
                    help="NAME, NAME@ROLE or NAME@ROLE/ms=40/db=-3; a name is "
                         "a bank entry, a starter instrument, an archetype "
                         "(or archetype:NAME); join with + or spaces")
    sp.add_argument("--roles", help="click,body,... one for each part that "
                                    "has no @ROLE, in order")
    sp.add_argument("--bank")
    sp.add_argument("--chord", help="the body is a chord: maj, min7, min9... "
                                    "or semitones 0,3,7,10")
    sp.add_argument("--root", type=int, default=0,
                    help="semitones from the key to the chord's root")
    sp.add_argument("--stereo", type=float,
                    help="a stereo sample: this many cents flat and sharp")
    sp.add_argument("--rev", action="store_true", help="play it backwards")
    sp.add_argument("--name")
    sp.add_argument("--role")
    sp.add_argument("--out")
    sp.add_argument("--mp3")

    sp = add("kit", "forge a drum kit", cmd_kit)
    sp.add_argument("name")
    sp.add_argument("--style", default="tight")
    sp.add_argument("--seed", type=int, default=1)
    sp.add_argument("--roles", help="kick,snare,hat,... (default: all)")
    sp.add_argument("--generations", type=int, default=2)
    sp.add_argument("--offspring", type=int, default=5)
    sp.add_argument("--out")
    sp.add_argument("--append", action="store_true")
    sp.add_argument("--mp3")

    sp = add("show", "read a bank", cmd_show)
    sp.add_argument("bank")
    sp.add_argument("name", nargs="?")
    sp.add_argument("--patch", action="store_true")
    sp = add("play", "render a bank to audio", cmd_play)
    sp.add_argument("bank")
    sp.add_argument("names", nargs="*")
    sp.add_argument("--out")

    sp = add("analyse", "where the channels of a module sit", cmd_analyse)
    sp.add_argument("score", help="a .sch score or a .it module")
    sp.add_argument("--fit", action="store_true", help="repair what it finds")
    sp.add_argument("--measure", action="store_true",
                    help="levels from real renders, one channel at a time")
    sp.add_argument("--rounds", type=int, default=4)
    sp.add_argument("--roles", help="ch04=bass,ch08=lead")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("-o", "--out", help="write the repaired module here")

    sp = add("lift", "measure an `inst` line", cmd_lift)
    sp.add_argument("recipe", help="'wave=saw oct=3-5 fade=60'")
    sp = add("starter", "rebuild the bank that ships with the forge",
             cmd_starter)
    sp.add_argument("--out")
    sp.add_argument("--kits", default="tight,808,dark")
    sp = add("director-prompt", "the question a model would be asked",
             cmd_director_prompt)
    sp.add_argument("scenario")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except CliError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except (bank_mod.BankError, KeyError, ValueError, DNAError) as error:
        print(f"error: {error.args[0] if error.args else error}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
