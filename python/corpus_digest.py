"""
corpus_digest.py — turn a corpus into something a model can actually read.

The transcribed corpus is 5.8 MB of tracker text: about 1.5 million tokens,
of which a 200k context holds thirty-one scores out of two hundred and
thirty-five. Handing a model the archive and asking it to "learn the style"
spends the whole budget on reading and leaves nothing to write with. Worse,
43% of every score is `...` padding — the model pays for the empty cells.

So this distils. It reads the whole corpus once, offline, and emits a study
pack of a few thousand tokens: what the music DOES, measured, plus a
handful of real excerpts to ground it. The measurements are the part that
compresses — one line saying "FM0 is the bass on 91% of tracks, median C#2"
replaces every bassline in the set.

## What is measured, and what is not

Reported because it measured strongly:

  * Which channel plays which role, and in what register.
  * Melodic interval distribution — how these composers actually move.
  * How many voices sound at once.
  * Tempo, vibrato depth and speed, velocity range.
  * Drum patterns, after each track's bar phase is recovered.

Left out because it did not:

  * Absolute onset position was uniform across the bar — 5.3% to 7.2% per
    row — until bar phase was recovered per track. A grid says where the
    ROWS are; it does not say which row is beat one. `_bar_phase` finds it
    from where the drums sit, and it only works on tracks whose drums
    concentrate somewhere, which is about two thirds of them. The rest are
    excluded from rhythm statistics rather than averaged in as noise.
"""

import collections
import json
import os
import statistics

import events as events_mod
import tracker as tracker_mod

#: Rows either side of a strong beat that still count as it. Drum machines
#: and human-entered patterns both land a little early or late.
_PHASE_SLACK = 0
#: A track's drums have to put at least this share of their hits on the
#: winning phase before its rhythm is trusted. Uniform hits would score
#: 1/bar, so this is several times chance.
PHASE_CONFIDENCE = 2.5


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return tracker_mod.loads(handle.read())


def _voice_of(event):
    E = events_mod
    if isinstance(event, E.FMNoteOn):
        return f"fm{event.channel}"
    if isinstance(event, E.PSGToneOn):
        return f"psg{event.channel}"
    if isinstance(event, E.OPLNoteOn):
        return f"opl{event.channel}"
    return None


def _bar_phase(drum_rows, bar):
    """Which row of the bar the drums think is beat one.

    Returns (offset, confidence). A grid tells you where the rows are and
    nothing about which one starts the bar; the drums do, when they are
    regular enough. Confidence is the share of hits on the winning phase
    against the 1/bar a uniform pattern would give.
    """
    if not drum_rows:
        return 0, 0.0
    hits = collections.Counter(row % bar for row in drum_rows)
    total = sum(hits.values())
    best = max(hits, key=lambda phase: hits[phase])
    return best, (hits[best] / total) * bar


def scan(score_paths, limit=None):
    """Read the corpus once and accumulate everything the digest reports."""
    stats = {
        "tracks": 0,
        "registers": collections.defaultdict(list),
        "voice_tracks": collections.Counter(),
        "intervals": collections.Counter(),
        "polyphony": collections.Counter(),
        "tempos": [],
        "velocities": collections.defaultdict(list),
        "vibrato": [],
        "rhythm": collections.Counter(),
        "rhythm_tracks": 0,
        "drum_rows": collections.Counter(),
        "drum_tracks": 0,
        "notes": 0,
    }

    for path in (score_paths[:limit] if limit else score_paths):
        try:
            events, meta = _load(path)
        except Exception:
            continue
        stats["tracks"] += 1
        stats["tempos"].append(meta.bpm)
        ticks_per_row = meta.ticks_per_row()
        bar = max(1, meta.lpb * 4)

        tick = 0
        last_pitch = {}
        rows = collections.defaultdict(set)
        drum_rows = []
        seen_voices = set()
        E = events_mod

        for event in events:
            if isinstance(event, E.Wait):
                tick += event.ticks
                continue
            if isinstance(event, E.End):
                break
            row = tick // ticks_per_row

            if isinstance(event, E.DACSample):
                drum_rows.append(row)
                continue
            if isinstance(event, E.Vibrato) and event.depth_cents:
                stats["vibrato"].append((event.depth_cents, event.speed_hz))
                continue

            voice = _voice_of(event)
            if voice is None:
                continue
            seen_voices.add(voice)
            pitch = (event.octave * 12
                     + events_mod.NOTE_NAMES.index(event.note))
            stats["registers"][voice].append(pitch)
            stats["notes"] += 1
            velocity = getattr(event, "velocity", None)
            if velocity is None:
                velocity = getattr(event, "volume", None)
            if velocity is not None:
                stats["velocities"][voice].append(velocity)
            if voice in last_pitch:
                step = pitch - last_pitch[voice]
                if abs(step) <= 24:
                    stats["intervals"][step] += 1
            last_pitch[voice] = pitch
            if voice.startswith("fm"):
                rows[row].add(voice)

        for voices in rows.values():
            stats["polyphony"][len(voices)] += 1
        for voice in seen_voices:
            stats["voice_tracks"][voice] += 1

        if len(drum_rows) >= 12:
            stats["drum_tracks"] += 1
            offset, confidence = _bar_phase(drum_rows, bar)
            if confidence >= PHASE_CONFIDENCE:
                stats["rhythm_tracks"] += 1
                for row in drum_rows:
                    # Reported on a sixteen-row bar whatever the track's
                    # own lpb, so patterns from different subdivisions can
                    # be pooled at all.
                    place = ((row - offset) % bar) * 16 // bar
                    stats["rhythm"][place] += 1
    return stats


# --------------------------------------------------------------------------
# Rendering the pack
# --------------------------------------------------------------------------
def _histogram(counts, keys, width=28):
    """A bar chart over `keys`, tolerant of a plain dict.

    Written against .get rather than requiring a Counter: the caller
    should not have to know which container this happens to use.
    """
    values = [counts.get(key, 0) for key in keys]
    total = sum(values) or 1
    peak = max(values, default=1) or 1
    return "\n".join(
        f"  {key:>4} {value / total * 100:5.1f}% "
        + "#" * max(0, int(round(value / peak * width)))
        for key, value in zip(keys, values))


def _note_name(midi):
    octave, index = divmod(int(round(midi)), 12)
    return f"{events_mod.NOTE_NAMES[index]}-{octave}"


def render(stats, exemplars=()):
    out = []
    add = out.append
    tracks = max(1, stats["tracks"])

    add("# What Mega Drive music does, measured")
    add("")
    add(f"From {stats['tracks']} transcribed tracks, {stats['notes']} notes. "
        f"Every number below is counted from the corpus, not asserted.")
    add("")

    # -- roles ---------------------------------------------------------------
    add("## Channel roles")
    add("")
    add("Median register and how often each channel is used at all. The "
        "roles are not a convention someone chose — they fall out of the "
        "chip: FM0 sits lowest on nearly every track, and FM5 is missing "
        "from most because the DAC takes channel 6 for drums.")
    add("")
    add("| voice | tracks | p10 | median | p90 | typical role |")
    add("|---|---|---|---|---|---|")
    ordered = sorted(stats["registers"],
                     key=lambda v: statistics.median(stats["registers"][v]))
    for voice in ordered:
        pitches = sorted(stats["registers"][voice])
        if len(pitches) < 50:
            continue
        low = pitches[len(pitches) // 10]
        mid = statistics.median(pitches)
        high = pitches[9 * len(pitches) // 10]
        share = stats["voice_tracks"][voice] / tracks
        role = ("bass" if mid < 45 else "mid / harmony" if mid < 57
                else "lead / top")
        add(f"| {voice} | {share*100:.0f}% | {_note_name(low)} | "
            f"{_note_name(mid)} | {_note_name(high)} | {role} |")
    add("")

    # -- melody --------------------------------------------------------------
    add("## How melodies move")
    add("")
    total = sum(stats["intervals"].values()) or 1
    common = sorted(stats["intervals"].items(), key=lambda kv: -kv[1])[:12]
    add("Interval from one note to the next on the same voice, in "
        "semitones:")
    add("")
    add("```")
    for step, count in sorted(common, key=lambda kv: kv[0]):
        add(f"  {step:+3d} {count/total*100:5.1f}%  "
            + "#" * int(round(count / common[0][1] * 30)))
    add("```")
    repeats = stats["intervals"][0] / total
    leaps = sum(c for s, c in stats["intervals"].items() if abs(s) >= 7) / total
    add("")
    steps = sum(c for s, c in stats["intervals"].items()
                if 0 < abs(s) <= 2) / total
    add(f"{repeats*100:.0f}% of moves repeat the note, {steps*100:.0f}% are a "
        f"step of a tone or less, and {leaps*100:.0f}% leap a fifth or more "
        f"— mostly octaves. There is very little in between. A line that "
        f"leaps constantly will not sound like this music, and neither will "
        f"one that never repeats a note.")
    add("")

    # -- density -------------------------------------------------------------
    add("## How many voices sound at once")
    add("")
    poly_total = sum(stats["polyphony"].values()) or 1
    add("```")
    for count in sorted(stats["polyphony"]):
        if count == 0:
            continue
        share = stats["polyphony"][count] / poly_total
        add(f"  {count} FM voice{'s' if count > 1 else ' '} {share*100:5.1f}%  "
            + "#" * int(round(share * 60)))
    add("```")
    add("")

    # -- rhythm --------------------------------------------------------------
    if stats["rhythm_tracks"]:
        add("## Where the drums fall")
        add("")
        add(f"From the {stats['rhythm_tracks']} of {stats['drum_tracks']} "
            f"drumming tracks whose bar phase could be recovered, folded "
            f"onto a sixteen-row bar. The rest are left out rather than "
            f"averaged in as noise — a grid says where the rows are, not "
            f"which row is beat one.")
        add("")
        add("```")
        add(_histogram(stats["rhythm"], list(range(16))))
        add("```")
        beat_rows = [0, 4, 8, 12]
        on_beat = sum(stats["rhythm"].get(r, 0) for r in beat_rows)
        rhythm_total = sum(stats["rhythm"].values()) or 1
        add("")
        add(f"{on_beat/rhythm_total*100:.0f}% of drum hits land on a "
            f"quarter-note beat (rows 0, 4, 8, 12), against the 25% they "
            f"would if placement were even.")
        add("")

    # -- expression ----------------------------------------------------------
    add("## Tempo, velocity, vibrato")
    add("")
    tempos = sorted(stats["tempos"])
    if tempos:
        add(f"- **Tempo**: {tempos[0]:.0f} to {tempos[-1]:.0f} BPM, median "
            f"{statistics.median(tempos):.0f}.")
    all_velocities = [v for values in stats["velocities"].values()
                      for v in values]
    fm_velocities = [v for voice, values in stats["velocities"].items()
                     if voice.startswith("fm") for v in values]
    if fm_velocities:
        ordered_v = sorted(fm_velocities)
        add(f"- **FM velocity**: median {statistics.median(ordered_v):.0f} of "
            f"127, with the middle 80% between "
            f"{ordered_v[len(ordered_v)//10]:.0f} and "
            f"{ordered_v[9*len(ordered_v)//10]:.0f}. Notes are not all "
            f"struck at full level.")
    if stats["vibrato"]:
        depths = sorted(d for d, _s in stats["vibrato"])
        speeds = sorted(s for _d, s in stats["vibrato"])
        add(f"- **Vibrato**: {len(stats['vibrato'])} spans. Depth median "
            f"{statistics.median(depths):.0f} cents (middle 80%: "
            f"{depths[len(depths)//10]:.0f}-{depths[9*len(depths)//10]:.0f}), "
            f"speed median {statistics.median(speeds):.1f} Hz. That is "
            f"well under a semitone — vibrato here colours a note, it does "
            f"not bend it.")
    add("")

    # -- exemplars -----------------------------------------------------------
    if exemplars:
        add("## Excerpts")
        add("")
        add("Real notation, so the numbers above have something to attach "
            "to. One passage per soundtrack, taken from its "
            "highest-confidence transcription.")
        add("")
        for title, text in exemplars:
            add(f"### {title}")
            add("")
            add("```")
            add(text.rstrip())
            add("```")
            add("")
    return "\n".join(out)


def pick_exemplars(manifest_path, bars=8, per_game=1):
    """One excerpt per soundtrack, from its best-transcribed track."""
    root = os.path.dirname(os.path.abspath(manifest_path))
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    entries = [e for e in manifest["entries"] if e.get("accepted")]

    import corpus_split
    by_game = collections.defaultdict(list)
    for entry in entries:
        by_game[corpus_split.game_of(entry)].append(entry)

    picked = []
    for game in sorted(by_game):
        best = sorted(by_game[game],
                      key=lambda e: (-e.get("grid_fit", 0),
                                     -e.get("notes", 0)))[:per_game]
        for entry in best:
            path = os.path.join(root, entry["score"])
            try:
                text = _excerpt(path, bars)
            except Exception:
                continue
            name = entry.get("gd3", {}).get("title") or entry["source"]
            picked.append((f"{game} — {name} "
                           f"({entry['bpm']:.0f} BPM, fit "
                           f"{entry['grid_fit']:.2f})", text))
    return picked


def _excerpt(path, bars):
    """The header plus `bars` bars from where the track gets busy.

    Taken from the densest stretch rather than the top: the first bars of
    a game track are usually one voice fading in, which teaches nothing
    about how the parts sit together.
    """
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    header, grid = [], []
    for line in lines:
        stripped = line.strip()
        if not grid and (not stripped or stripped.startswith("#")
                         or stripped.split()[0] in tracker_mod.DIRECTIVES):
            header.append(line)
        else:
            grid.append(line)

    rows = [line for line in grid if line.strip()]
    window = bars * 16
    if len(rows) <= window:
        return "\n".join(header + rows)
    best_start, best_filled = 0, -1
    for start in range(0, len(rows) - window, 16):
        filled = sum(1 for line in rows[start:start + window]
                     for cell in line.split() if cell not in ("...", "==="))
        if filled > best_filled:
            best_start, best_filled = start, filled
    excerpt = rows[best_start:best_start + window]
    return "\n".join(header + [f"# ... from row {best_start} ..."] + excerpt)


def main(argv):
    import argparse
    import glob as glob_mod

    parser = argparse.ArgumentParser(
        prog="corpus_digest",
        description="Distil a corpus into a study pack a model can read.")
    parser.add_argument("corpus", help="a corpus directory (with manifest.json)")
    parser.add_argument("-o", "--out", default="STUDY.md")
    parser.add_argument("--bars", type=int, default=8,
                        help="bars per excerpt")
    parser.add_argument("--limit", type=int, help="scan only N scores")
    parser.add_argument("--no-excerpts", action="store_true")
    args = parser.parse_args(argv)

    manifest = os.path.join(args.corpus, "manifest.json")
    scores = sorted(glob_mod.glob(os.path.join(args.corpus, "scores", "*.trk")))
    if not scores:
        print(f"no scores under {args.corpus}/scores")
        return 1

    stats = scan(scores, limit=args.limit)
    exemplars = ()
    if not args.no_excerpts and os.path.exists(manifest):
        exemplars = pick_exemplars(manifest, bars=args.bars)
    text = render(stats, exemplars)

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    characters = len(text)
    print(f"scanned {stats['tracks']} scores, {stats['notes']} notes")
    print(f"wrote {args.out}: {characters/1024:.1f} KB, "
          f"~{characters/4/1000:.1f}k tokens at 4 chars/token")
    if exemplars:
        print(f"  including {len(exemplars)} excerpts of {args.bars} bars")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
