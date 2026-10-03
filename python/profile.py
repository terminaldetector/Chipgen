"""
profile.py — the verification step sanity.py structurally cannot do:
listen to the actual audio, section by section.

sanity.py checks the EVENT LIST before rendering, and it is good at what
it checks: a channel with no rest across the WHOLE track. It is exactly
as good as that description and no better, because it only ever sees
whole-track aggregates. A channel that is busy somewhere in a 40-second
piece — an arpeggio here, a bass pulse there — clears
RETRIGGER_CEILING comfortably even if, in one specific section, a single
held note leaked in from the section before it because a pattern
generator wrote "..." (hold) where it meant "===" (off). The aggregate
looks fine. The section does not.

Two independent sessions hand-rolled the same fix for this: render the
WAV, then eyeball RMS per bar with a throwaway numpy script, because a
section that was supposed to drop to near-silence for a breakdown and
didn't shows up immediately as a flat RMS curve where a dip belongs. One
session's own notes on the friction: "такого инструмента в репозитории
нет" — there's no such tool in the repo. This is that tool, in the
"repository" rather than reinvented per session.

Two ways to get segment boundaries:

  * Marker events. events.Marker already exists in the vocabulary and
    rendered to nothing before this — it costs a composer one line per
    section ("intro", "drop", "breakdown", ...) and turns into exactly
    the section boundaries a human would ask about, by name, in the
    output. This is the version worth using when you architected the
    track in sections to begin with (which sanity.py's own docstring,
    and both session logs above, describe doing).
  * Fixed bar length, when there are no markers. Coarser — you get "bar
    14" instead of "breakdown" — but it costs nothing to add and needs
    no cooperation from the composer.

profile() takes rendered audio, not events: RMS is fundamentally a
measurement of the audio, and this exists specifically because
sanity.check()'s event-level view could not have caught the bug that
motivated it.
"""

from typing import List, NamedTuple, Optional

import audio as _audio
import events as events_mod


class Segment(NamedTuple):
    label: str
    start: float    # seconds
    end: float      # seconds


class SegmentStats(NamedTuple):
    label: str
    start: float
    end: float
    rms: float
    peak: float

    def __repr__(self):
        return (f"{self.label:<16s} {self.start:6.2f}-{self.end:6.2f}s  "
                f"rms={self.rms:.4f}  peak={self.peak:.4f}")


def segments_from_markers(events: List[events_mod.Event],
                          ticks_per_second: float = 192.0) -> List[Segment]:
    """One segment per Marker, running from that marker to the next (or to
    End). A label defaults to "1", "2", ... when the composer left it blank.
    Returns [] if there are fewer than two markers — not enough to say
    anything a single whole-track number wouldn't already say.
    """
    E = events_mod
    rate = ticks_per_second
    clock = 0.0
    marks: List[tuple] = []   # (label, start_time)

    for event in events:
        if isinstance(event, E.Wait):
            clock += event.ticks / rate
        elif isinstance(event, E.Tempo):
            rate = max(1.0, float(event.ticks_per_second))
        elif isinstance(event, E.Marker):
            marks.append((event.label or str(len(marks) + 1), clock))
        elif isinstance(event, E.End):
            break

    if len(marks) < 2:
        return []

    segments = [Segment(label, start, end)
               for (label, start), (_, end) in zip(marks, marks[1:])]
    segments.append(Segment(marks[-1][0], marks[-1][1], clock))
    return segments


def bar_segments(duration: float, bpm: float, beats_per_bar: int = 4) -> List[Segment]:
    """Fixed-length segments, one per bar, when there are no markers to name
    them by. `duration` and the returned times are seconds."""
    bar_seconds = beats_per_bar * 60.0 / bpm
    if bar_seconds <= 0:
        return []
    segments = []
    t = 0.0
    n = 0
    while t < duration:
        end = min(t + bar_seconds, duration)
        segments.append(Segment(str(n), t, end))
        t = end
        n += 1
    return segments


def profile(buf, sample_rate: int, segments: List[Segment]) -> List[SegmentStats]:
    """RMS and peak per segment. `buf` is whatever Sequencer.render() or
    chipgen.compose() returned — a numpy array or an audio.Buffer, either
    works, same as everywhere else in this project."""
    stats = []
    for segment in segments:
        start = max(0, int(segment.start * sample_rate))
        end = min(len(buf), int(segment.end * sample_rate))
        if end <= start:
            stats.append(SegmentStats(segment.label, segment.start, segment.end,
                                      0.0, 0.0))
            continue
        chunk = buf[start:end]
        stats.append(SegmentStats(segment.label, segment.start, segment.end,
                                  _audio.rms(chunk), _audio.peak(chunk)))
    return stats


def format_table(stats: List[SegmentStats]) -> str:
    if not stats:
        return "(no segments)"
    width = max(len(s.label) for s in stats)
    lines = [f"{'segment':<{width}s}  {'time':>13s}  {'rms':>7s}  {'peak':>7s}"]
    for s in stats:
        span = f"{s.start:5.1f}-{s.end:5.1f}s"
        bar = "#" * max(1, round(s.rms * 40)) if s.rms > 0 else ""
        lines.append(f"{s.label:<{width}s}  {span:>13s}  {s.rms:7.4f}  "
                     f"{s.peak:7.4f}  {bar}")
    return "\n".join(lines)


def auto_profile(buf, sample_rate: int, events: List[events_mod.Event] = None,
                 ticks_per_second: float = 192.0, bpm: Optional[float] = None,
                 beats_per_bar: int = 4) -> List[SegmentStats]:
    """Marker segments if the score has at least two; otherwise fixed bars
    if a bpm was given; otherwise empty (nothing sensible to divide by)."""
    if events is not None:
        marked = segments_from_markers(events, ticks_per_second)
        if marked:
            return profile(buf, sample_rate, marked)
    if bpm:
        duration = len(buf) / float(sample_rate)
        return profile(buf, sample_rate, bar_segments(duration, bpm, beats_per_bar))
    return []
