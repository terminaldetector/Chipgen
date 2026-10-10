"""selection.py — pick instruments by what they measure, not what they are called.

The gap this closes: "write me something aggressive" is a real instruction
and the bank cannot answer it. A model reads the patch names, decides
`techno_bass` sounds aggressive, and is choosing by vocabulary. Tagging the
patches by genre moves the guess rather than removing it — a tag is one
person's opinion, it does not transfer to a bank imported from a VGM five
minutes ago, and nothing checks it.

So genre and role are expressed here as target positions on axes that were
MEASURED for every patch: how low its energy sits, how bright it is, how
fast it starts, how long it rings, how dense its harmonics are. A patch
scores well for "aggressive lead" when it is bright, sharp and dense
relative to the rest of the bank, and the score comes with the numbers that
produced it.

**Ranks, not absolute thresholds.** Every axis is converted to the patch's
rank within its own bank, 0.0 for the lowest and 1.0 for the highest. This
is the part that makes it survive contact with an imported bank: a set of
600 Furnace patches has a completely different absolute brightness range
than the 19 built in here, and a fixed "bright means over 5.0" would either
select everything or nothing. A rank always spreads across the bank you
actually have.

Consequence worth knowing: ranks are relative, so the top-ranked bass in a
bank of pads is still a pad. `score` says how well a patch fits the target
COMPARED TO ITS BANK, not in absolute terms, and the reason string carries
the raw measurements so the caller can see the difference.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import audition

#: The axes selection reasons over. Each maps to a key in the audition
#: summary; the phrase describes the HIGH end of the scale, for reasons.
#:
#: Which axes are here was decided by measuring the bank and throwing out
#: the ones that do not separate it. Energy above 2 kHz at C4 reads 0.000
#: at the median across all 19 patches — the chip simply does not put much
#: there — so scoring on it would have been scoring on noise. Attack is
#: kept but weighted lightly everywhere except pad, because 12 of 19
#: patches use attack rate 31 and tie at 0 ms; it separates strings and
#: jazz_chord_pad from the field and nothing else.
AXES = {
    "low":        ("low_at_c2", "more low end at C2"),
    "presence":   ("mid_at_c4", "more midrange at C4"),
    "brightness": ("brightness", "brighter"),
    "attack":     ("attack", "slower to start"),
    "release":    ("release", "longer ring"),
    "harmonics":  ("harmonics", "denser harmonically"),
    "dynamics":   ("velocity_range_db", "more velocity range"),
}

#: What each musical role wants, as (target rank 0..1, weight). A weight of
#: 0 leaves the axis out. These are judgement calls over measured
#: quantities — argue with them by editing this table, which is why it is
#: one table and not spread through the code.
ROLES = {
    "bass":     {"brightness": (0.10, 3.0), "presence": (0.15, 2.0),
                 "low": (0.90, 1.5), "release": (0.40, 0.8)},
    "lead":     {"brightness": (0.85, 3.0), "presence": (0.75, 2.0),
                 "harmonics": (0.70, 1.5), "dynamics": (0.70, 0.5)},
    "pad":      {"release": (1.00, 3.0), "attack": (1.00, 2.5),
                 "brightness": (0.45, 1.0), "presence": (0.60, 1.0)},
    "pluck":    {"release": (0.05, 3.0), "brightness": (0.60, 1.2),
                 "attack": (0.00, 1.0), "presence": (0.50, 0.8)},
    "harmony":  {"presence": (0.70, 1.5), "release": (0.60, 1.5),
                 "brightness": (0.40, 1.5), "harmonics": (0.30, 0.8)},
    "bell":     {"brightness": (0.95, 2.5), "harmonics": (0.80, 1.5),
                 "release": (0.70, 1.5), "presence": (0.80, 1.0)},
    "stab":     {"release": (0.15, 2.5), "harmonics": (0.80, 1.5),
                 "brightness": (0.70, 1.5), "presence": (0.60, 1.0)},
}

#: Roles that only make sense if the patch can actually carry the register.
#: A rank is relative, so without this the best-ranked patch in a bank of
#: leads is still a lead when you ask for a bass.
REQUIRES_NOTE = {"bass": "C-2"}

#: A genre shifts the role's targets along the same axes. Values are added
#: to the target rank and clamped, so "hardcore" means "whatever this role
#: normally wants, but brighter, sharper and tighter than usual".
#:
#: This is the whole mechanism by which a genre word reaches the chip. It
#: is deliberately small and readable: a genre is not a mood here, it is a
#: handful of decisions about brightness, attack and decay that anyone can
#: check against the render.
GENRES = {
    "hardcore":    {"brightness": +0.25, "attack": -0.30, "release": -0.25,
                    "harmonics": +0.25},
    "industrial":  {"brightness": +0.20, "attack": -0.20, "release": -0.15,
                    "harmonics": +0.30},
    "dark_action": {"brightness": +0.15, "attack": -0.20, "harmonics": +0.20,
                    "low": +0.10},
    "boss":        {"brightness": +0.20, "attack": -0.25, "harmonics": +0.25,
                    "low": +0.10},
    "retro_arcade": {"brightness": +0.15, "attack": -0.30,
                     "harmonics": -0.25, "release": -0.20},
    "funk":        {"attack": -0.30, "release": -0.20, "brightness": +0.05,
                    "dynamics": +0.20},
    "melodic":     {"brightness": -0.15, "release": +0.20, "attack": +0.10},
    "jrpg":        {"brightness": -0.10, "release": +0.25, "attack": +0.15,
                    "harmonics": -0.10},
    "ambient":     {"attack": +0.35, "release": +0.35, "brightness": -0.25,
                    "harmonics": -0.20},
}


def _ranks(characteristics: dict) -> dict:
    """{patch: {axis: rank 0..1}} — position within this bank, per axis."""
    usable = {n: c for n, c in characteristics.items() if c.get("usable")}
    out = {name: {} for name in usable}
    for axis, (key, _) in AXES.items():
        pairs = [(n, c.get(key)) for n, c in usable.items()
                 if c.get(key) is not None]
        pairs.sort(key=lambda p: p[1])
        if len(pairs) < 2:
            for name, _ in pairs:
                out[name][axis] = 0.5
            continue
        # Average rank over ties, so five identical patches do not get
        # spread from 0.0 to 1.0 by list order alone.
        i = 0
        while i < len(pairs):
            j = i
            while j + 1 < len(pairs) and pairs[j + 1][1] == pairs[i][1]:
                j += 1
            rank = ((i + j) / 2.0) / (len(pairs) - 1)
            for k in range(i, j + 1):
                out[pairs[k][0]][axis] = rank
            i = j + 1
    return out


def targets(role: str, genre: str = None) -> dict:
    """-> {axis: (target rank, weight)} for a role, shifted by a genre."""
    if role not in ROLES:
        raise KeyError(f"unknown role {role!r}; have: {', '.join(sorted(ROLES))}")
    spec = {axis: [target, weight]
            for axis, (target, weight) in ROLES[role].items()}
    if genre:
        if genre not in GENRES:
            raise KeyError(f"unknown genre {genre!r}; have: "
                           f"{', '.join(sorted(GENRES))}")
        for axis, delta in GENRES[genre].items():
            if axis in spec:
                # A genre nudges a defining axis; it does not invert one.
                # Un-damped, "hardcore pad" shifted the attack target far
                # enough to select a 0 ms attack with a 107 ms release,
                # which is not a pad in any genre. Axes the role leans on
                # hardest (weight 2.5+) move at 40% of the genre's delta.
                damping = 0.4 if spec[axis][1] >= 2.5 else 1.0
                spec[axis][0] = max(0.0, min(1.0,
                                             spec[axis][0] + delta * damping))
            else:
                # The genre cares about an axis this role is neutral on.
                # Include it, but lightly — the role still leads.
                spec[axis] = [max(0.0, min(1.0, 0.5 + delta)), 0.5]
    return {axis: (t, w) for axis, (t, w) in spec.items()}


def score(patch_ranks: dict, spec: dict):
    """-> (0..1 fit, [reasons]). 1.0 means every axis is exactly on target."""
    total_weight = sum(w for _, w in spec.values())
    if total_weight <= 0:
        return 0.0, []
    penalty = 0.0
    notes = []
    for axis, (target, weight) in sorted(spec.items(),
                                         key=lambda kv: -kv[1][1]):
        rank = patch_ranks.get(axis)
        if rank is None:
            penalty += weight          # missing measurement is not a pass
            continue
        distance = abs(rank - target)
        penalty += weight * distance
        if weight >= 1.0 and distance <= 0.25:
            notes.append(axis)
    return max(0.0, 1.0 - penalty / total_weight), notes


def cast(role: str, genre: str = None, characteristics: dict = None,
         limit: int = 3, exclude=()) -> list:
    """Rank the bank for one role. -> [{name, score, why, measurements}].

    `exclude` keeps a palette from putting the same patch in two chairs.
    """
    characteristics = characteristics or audition.characteristics()
    ranks = _ranks(characteristics)
    spec = targets(role, genre)
    required_note = REQUIRES_NOTE.get(role)
    rows = []
    for name, patch_ranks in ranks.items():
        if name in exclude:
            continue
        if required_note and required_note not in \
                (characteristics[name].get("audible_notes") or ()):
            continue
        fit, matched = score(patch_ranks, spec)
        rows.append({
            "name": name,
            "score": fit,
            "role": role,
            "genre": genre,
            "matched": matched,
            "why": _why(name, characteristics[name], matched),
            "measurements": characteristics[name],
        })
    rows.sort(key=lambda r: (-r["score"], r["name"]))
    return rows[:limit]


def _why(name: str, c: dict, matched) -> str:
    """A sentence built from the measurements, not from the patch's name."""
    bits = []
    if "low" in matched and c.get("low_at_c2") is not None:
        bits.append(f"{c['low_at_c2'] * 100:.0f}% of its energy under 200 Hz at C2")
    if "presence" in matched and c.get("mid_at_c4") is not None:
        bits.append(f"{c['mid_at_c4'] * 100:.0f}% midrange at C4")
    if "brightness" in matched and c.get("brightness") is not None:
        bits.append(f"centre of mass {c['brightness']:.1f}x the fundamental")
    if "attack" in matched and c.get("attack") is not None:
        bits.append(f"{c['attack'] * 1000:.0f} ms attack")
    if "release" in matched and c.get("release") is not None:
        bits.append(f"{c['release'] * 1000:.0f} ms release")
    if "harmonics" in matched and c.get("harmonics") is not None:
        bits.append(f"{c['harmonics']:.0f} harmonics")
    if "dynamics" in matched and c.get("velocity_range_db") is not None:
        bits.append(f"{c['velocity_range_db']:.0f} dB of velocity range")
    return "; ".join(bits) if bits else "closest available fit"


#: A sensible default cast for a piece: who plays what, in channel order.
DEFAULT_PARTS = ("bass", "lead", "harmony", "pad")


def palette(genre: str = None, parts=DEFAULT_PARTS,
            characteristics: dict = None) -> dict:
    """Assign one patch per part, without reusing a patch. -> {part: choice}.

    Filled in the order given, so put the parts that matter most first —
    the bass gets first pick of the bank.
    """
    characteristics = characteristics or audition.characteristics()
    chosen, used = {}, set()
    for part in parts:
        picks = cast(part, genre, characteristics, limit=1, exclude=used)
        if picks:
            chosen[part] = picks[0]
            used.add(picks[0]["name"])
    return chosen


def format_cast(rows) -> str:
    lines = []
    for r in rows:
        lines.append(f"  {r['score']:.2f}  {r['name']:16s} {r['why']}")
    return "\n".join(lines)


def format_palette(chosen: dict) -> str:
    lines = []
    for part, r in chosen.items():
        lines.append(f"{part:9s} -> {r['name']:16s} (fit {r['score']:.2f})")
        lines.append(f"          {r['why']}")
    return "\n".join(lines)


def main(argv):
    import argparse
    import json
    parser = argparse.ArgumentParser(
        description="choose instruments by measurement")
    parser.add_argument("--role", help=f"one of: {', '.join(sorted(ROLES))}")
    parser.add_argument("--genre", help=f"one of: {', '.join(sorted(GENRES))}")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--palette", action="store_true",
                        help="cast a whole arrangement instead of one role")
    parser.add_argument("--parts", default=",".join(DEFAULT_PARTS))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.palette or not args.role:
        chosen = palette(args.genre, tuple(p.strip()
                                           for p in args.parts.split(",") if p.strip()))
        if args.json:
            print(json.dumps({k: {kk: vv for kk, vv in v.items()
                                  if kk != "measurements"}
                              for k, v in chosen.items()}, indent=1))
        else:
            title = f"palette for {args.genre or 'no genre'}"
            print(title)
            print("-" * len(title))
            print(format_palette(chosen))
        return 0

    rows = cast(args.role, args.genre, limit=args.limit)
    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "measurements"}
                          for r in rows], indent=1))
    else:
        print(f"{args.role}" + (f" for {args.genre}" if args.genre else ""))
        print(format_cast(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
