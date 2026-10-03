"""python3 -m schism — build, check and inspect.

    python3 -m schism build song.sch -o song.it [--wav song.wav] [--mp3 song.mp3]
                            [--engine openmpt|schism]
    python3 -m schism check song.sch
    python3 -m schism info song.it
"""

import argparse
import os
import sys

from . import it_read, it_write, notation, openmpt, render as render_module, \
    schismtracker, verify


def _build(args) -> int:
    try:
        module, report = notation.compile_file(args.score)
    except notation.NotationError as error:
        print(error, file=sys.stderr)
        return 2
    blob = it_write.build(module)
    print(report.text())
    out = args.output or os.path.splitext(args.score)[0] + ".it"
    with open(out, "wb") as handle:
        handle.write(blob)
    print(f"wrote {out} ({len(blob) / 1024:.0f} KB)")

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
        print(error, file=sys.stderr)
        return 2
    print(report.text())
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
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="schism")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="score -> .it (and WAV/MP3)")
    build.add_argument("score")
    build.add_argument("-o", "--output")
    build.add_argument("--wav")
    build.add_argument("--mp3")
    build.add_argument("--no-verify", action="store_true")
    build.add_argument("--engine", choices=render_module.ENGINES,
                       default="openmpt",
                       help="who plays the module for --wav/--mp3: libopenmpt "
                            "or Schism Tracker itself")
    build.set_defaults(run=_build)
    check = sub.add_parser("check", help="compile only; list every problem")
    check.add_argument("score")
    check.set_defaults(run=_check)
    info = sub.add_parser("info", help="describe a .it file")
    info.add_argument("module")
    info.set_defaults(run=_info)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
