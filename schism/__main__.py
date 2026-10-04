"""python3 -m schism — build, check and inspect.

    python3 -m schism build song.sch -o song.it [--wav song.wav] [--mp3 song.mp3]
                            [--engine openmpt|schism] [--peak TARGET|off]
                            [--strict] [--preset nes|sega|schism]
                            [--no-sidecar]
    python3 -m schism check song.sch [--strict]
    python3 -m schism info song.it
    python3 -m schism forge ...        generate instruments, drums and
                                       effects (python3 -m schism forge -h)
"""

import argparse
import json
import os
import sys

from . import export, it_read, it_write, notation, openmpt, \
    render as render_module, schismtracker, verify

#: the peak a build fits the mix volume to, unless the score says `mv`
PEAK_DEFAULT = 0.89


def _peak_target(text, report):
    """--peak: `auto` (fit unless the score set `mv`), `off`, or a number.
    -> the target, or None for "leave the mix volume alone"."""
    text = (text or "auto").strip().lower()
    if text == "off":
        return None
    if text == "auto":
        return None if report.mv_set else PEAK_DEFAULT
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"--peak wants a number, `auto` or `off`, not "
                         f"{text!r}") from None
    if not 0.05 <= value <= 1.0:
        raise ValueError(f"--peak {value:g}: a peak is between 0.05 and 1")
    return value


def _build(args) -> int:
    try:
        module, report = notation.compile_file(args.score)
    except notation.NotationError as error:
        print(error, file=sys.stderr)
        return 2
    print(report.text())
    if args.strict and report.warnings:
        many = len(report.warnings) != 1
        print(f"strict: {len(report.warnings)} warning{'s' if many else ''} "
              f"stop{'' if many else 's'} the build; nothing was written",
              file=sys.stderr)
        return 5
    notes = []
    if args.preset:
        try:
            notes = export.apply_preset(module, args.preset)
        except export.ExportError as error:
            print(f"preset {args.preset} does not take this module:",
                  file=sys.stderr)
            for problem in error.problems:
                print(f"  {problem}", file=sys.stderr)
            return 6
        for note in notes:
            print(f"preset {args.preset}: {note}")
    peak = None
    try:
        target = _peak_target(args.peak, report)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if target is not None:
        if not openmpt.available():
            print("peak not fitted: libopenmpt is not installed (apt install "
                  "libopenmpt0), so the mix volume stays as it is")
        else:
            try:
                peak = render_module.fit_peak(module, target)
                print(peak.text())
            except render_module.RenderError as error:
                print(f"peak not fitted: {error}")
    elif report.mv_set:
        print(f"mix volume {module.mix_volume}, as the score says")
    blob = it_write.build(module)
    out = args.output or os.path.splitext(args.score)[0] + ".it"
    with open(out, "wb") as handle:
        handle.write(blob)
    print(f"wrote {out} ({len(blob) / 1024:.0f} KB)")
    if not args.no_sidecar:
        side = export.write_sidecar(
            export.sidecar_path(out),
            export.sidecar(module, file=os.path.basename(out), peak=peak,
                           preset=args.preset, notes=notes))
        print(f"wrote {side}")

    if openmpt.available() and not args.no_verify:
        problems = verify.compare(module, blob)
        if problems:
            print("libopenmpt reads the module differently from how it was "
                  "written:", file=sys.stderr)
            for problem in problems[:10]:
                print("  " + problem, file=sys.stderr)
            return 3
        with openmpt.Song(blob) as song:
            print(f"libopenmpt {openmpt.version()} reads it identically: "
                  f"{song.channels} channels, {song.patterns} patterns, "
                  f"{song.duration:.1f} s")
    elif not openmpt.available():
        print("libopenmpt is not installed, so the module was not checked "
              "against a second reader (apt install libopenmpt0)")

    if args.wav or args.mp3:
        from . import render
        try:
            if args.wav:
                seconds, peak = render.to_wav(blob, args.wav,
                                              engine=args.engine)
                print(f"wrote {args.wav} ({seconds:.1f} s, peak {peak:.2f}, "
                      f"{args.engine})")
            if args.mp3:
                seconds, peak, encoder = render.render_mp3(
                    blob, args.mp3, title=module.title, artist="schism-chipgen",
                    engine=args.engine)
                print(f"wrote {args.mp3} ({os.path.getsize(args.mp3) / 1024:.0f}"
                      f" KB, {encoder}, peak {peak:.2f}, {args.engine})")
        except (openmpt.OpenMPTError, schismtracker.SchismError,
                render.RenderError) as error:
            print(f"cannot render: {error}", file=sys.stderr)
            return 4
    return 0


def _check(args) -> int:
    try:
        _, report = notation.compile_file(args.score)
    except notation.NotationError as error:
        if args.json:
            print(json.dumps({"ok": False, "problems": [
                d._asdict() for d in error.diagnostics]}, indent=1))
        else:
            print(error, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ok": True, "warnings": list(report.warnings),
                          "strict_ok": not report.warnings}, indent=1))
        return 5 if args.strict and report.warnings else 0
    print(report.text())
    if report.warnings:
        count = len(report.warnings)
        print(f"PLAYABLE, WITH {count} WARNING{'S' if count != 1 else ''}: "
              f"each one is something that compiles and then plays "
              f"nothing, or less than was written")
        if args.strict:
            print("strict: not accepted", file=sys.stderr)
            return 5
        return 0
    print("PLAYABLE")
    return 0


def _info(args) -> int:
    module = it_read.read_file(args.module, load_data=False)
    print(f"{module.title!r}: {len(module.orders)} orders, "
          f"{len(module.patterns)} patterns, {len(module.instruments)} "
          f"instruments, {len(module.samples)} samples, {module.channels_used()}"
          f" channels, speed {module.speed} tempo {module.tempo}")
    for number, ins in enumerate(module.instruments, 1):
        print(f"  inst {number:02d} {ins.name}")
    for number, smp in enumerate(module.samples, 1):
        flag = " (compressed)" if smp.compressed else ""
        print(f"  smp  {number:02d} {smp.name} c5speed {smp.c5speed}{flag}")
    for line in export.kit_lines(module):
        print(f"  keys {line}")
    return 0


def _explain(args) -> int:
    """An instrument as the players will run it: envelopes in ticks and in
    ms at the module's tempo, what a key-off and the next note do."""
    from .forge import program
    if args.module.lower().endswith(".it"):
        module = it_read.read_file(args.module)
    else:
        try:
            module, _ = notation.compile_file(args.module)
        except notation.NotationError as error:
            print(error, file=sys.stderr)
            return 2
    numbers = [args.instrument] if args.instrument else \
        [n for n, ins in enumerate(module.instruments, 1) if ins.name]
    out = [program.explain_instrument(module, n) for n in numbers]
    if args.json:
        print(json.dumps(out if len(out) != 1 else out[0], indent=1))
        return 0
    for info in out:
        print(f"inst {info['instrument']:02d} {info['name']} (tempo "
              f"{info['tempo']}, a tick is {info['tick_ms']:g} ms)")
        for slot in ("volume", "pan", "pitch_or_filter"):
            env = info[slot]
            if env is None:
                continue
            nodes = " ".join(f"{n['value']}@{n['ms']:g}ms" for n in
                             env["nodes"])
            print(f"  {env['slot']:7s} {nodes}"
                  + (f"  [{env['held']}]" if env.get("held") else ""))
        print(f"  nna {info['nna']}, dct {info['dct']}, dca {info['dca']}, "
              f"fade {info['fadeout']}")
        for note in info["notes"]:
            print(f"  - {note}")
    return 0


def _corpus(args) -> int:
    from . import corpus
    try:
        if args.action == "index":
            manifest = None
            if args.manifest:
                with open(args.manifest, encoding="utf-8") as handle:
                    manifest = json.load(handle)
            entries = corpus.index(args.files, manifest, limit_s=args.limit)
            if args.out:
                corpus.write_index(entries, args.out)
                print(f"wrote {args.out}: {len(entries)} modules")
            else:
                for entry in entries:
                    print(json.dumps(entry, ensure_ascii=False))
            return 0
        if args.action == "trace":
            with corpus.Module(args.files[0]) as module:
                stored = corpus.static(module)
                played = corpus.trace(module, args.limit)
            summary = {"file": args.files[0], "stored": stored,
                       "reached": played["reached"],
                       "rows_played": len(played["rows"]),
                       "seconds": played["seconds"],
                       "stopped": played["stopped"]}
            if args.all_rows:
                summary["rows"] = played["rows"]
            print(json.dumps(summary, indent=1))
            return 0
        ep = corpus.episode(args.files[0], args.order, args.row, args.rows,
                            args.context,
                            [int(c) - 1 for c in args.channels.split(",")]
                            if args.channels else None)
        print(json.dumps(ep, indent=1, ensure_ascii=False))
        return 0
    except (corpus.CorpusError, OSError) as error:
        print(f"schism corpus: {error}", file=sys.stderr)
        return 2


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "forge":
        from .forge import cli
        return cli.main(argv[1:])
    parser = argparse.ArgumentParser(prog="schism")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="score -> .it (and WAV/MP3)")
    build.add_argument("score")
    build.add_argument("-o", "--output")
    build.add_argument("--wav")
    build.add_argument("--mp3")
    build.add_argument("--no-verify", action="store_true")
    build.add_argument("--peak", default="auto", metavar="TARGET",
                       help="fit the mix volume so the libopenmpt render "
                            "peaks at TARGET (0.05..1), or `off`; the "
                            "default, `auto`, fits to 0.89 unless the score "
                            "sets `mv`. Only the header moves, no sample is "
                            "touched")
    build.add_argument("--strict", action="store_true",
                       help="any warning stops the build (exit 5): a note "
                            "with no sample, a key-off that ends nothing...")
    build.add_argument("--preset", choices=sorted(export.PRESETS),
                       help="hold the module to a console's limits: "
                            + "; ".join(f"{k}: {v[1]}"
                                        for k, v in sorted(export.PRESETS.items())))
    build.add_argument("--no-sidecar", action="store_true",
                       help="do not write SONG.it.json beside the module")
    build.add_argument("--engine", choices=render_module.ENGINES,
                       default="openmpt",
                       help="who plays the module for --wav/--mp3: libopenmpt "
                            "or Schism Tracker itself")
    build.set_defaults(run=_build)
    check = sub.add_parser("check", help="compile only; list every problem")
    check.add_argument("score")
    check.add_argument("--strict", action="store_true",
                       help="a warning is a failure (exit 5)")
    check.add_argument("--json", action="store_true",
                       help="problems and warnings as JSON, each with its "
                            "line and fix")
    check.set_defaults(run=_check)
    explain = sub.add_parser("explain", help="an instrument as the players "
                             "run it: envelopes in ticks and ms, key-off, NNA")
    explain.add_argument("module", help="a .sch score or a .it module")
    explain.add_argument("instrument", nargs="?", type=int)
    explain.add_argument("--json", action="store_true")
    explain.set_defaults(run=_explain)
    corp = sub.add_parser("corpus", help="index modules, trace what plays, "
                          "read an episode (any format libopenmpt plays)")
    corp.add_argument("action", choices=("index", "trace", "episode"))
    corp.add_argument("files", nargs="+")
    corp.add_argument("-o", "--out", help="index: write JSON lines here")
    corp.add_argument("--manifest", help="index: {file: {author, source, "
                      "licence...}} carried into each entry")
    corp.add_argument("--limit", type=float, default=1200.0,
                      help="seconds of song to trace at most (a song that "
                           "loops forever stops here)")
    corp.add_argument("--rows", type=int, default=12,
                      help="episode: rows to read (4-32)")
    corp.add_argument("--all-rows", action="store_true",
                      help="trace: print every row played, in order")
    corp.add_argument("--order", type=int, default=0)
    corp.add_argument("--row", type=int, default=0)
    corp.add_argument("--context", type=int, default=2)
    corp.add_argument("--channels", help="episode: 1,3,4")
    corp.set_defaults(run=_corpus)
    info = sub.add_parser("info", help="describe a .it file")
    info.add_argument("module")
    info.set_defaults(run=_info)
    # `forge` was handled above; it is listed so --help says it exists
    sub.add_parser("forge", help="instruments, drums and effects made to a "
                                 "description (python3 -m schism forge -h)")
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    try:
        code = main()
        sys.stdout.flush()
    except BrokenPipeError:
        # `python3 -m schism ... | head`: the reader left early, which is
        # its right and not our failure. Point stdout at /dev/null so the
        # flush at exit has nothing to complain about.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        code = 0
    sys.exit(code)
