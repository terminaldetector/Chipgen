"""The event vocabulary: strict round-trips, and the repairs a model needs."""

import json

import events as E


def test_every_type_round_trips_through_dict():
    samples = {
        "Wait": E.Wait(ticks=24),
        "Tempo": E.Tempo(ticks_per_second=96.0),
        "LoopPoint": E.LoopPoint(),
        "Marker": E.Marker(label="chorus"),
        "FMInstrumentSelect": E.FMInstrumentSelect(channel=0, instrument="bass"),
        "FMNoteOn": E.FMNoteOn(channel=1, note="A#", octave=3, velocity=90),
        "FMNoteOff": E.FMNoteOff(channel=1),
        "FMPan": E.FMPan(channel=2, left=True, right=False, ams=1, pms=3),
        "FMLFO": E.FMLFO(enable=True, freq=5),
        "FMVolume": E.FMVolume(channel=3, volume=64),
        "FMPitch": E.FMPitch(channel=4, cents=-25.5),
        "DACEnable": E.DACEnable(enable=True),
        "DACSample": E.DACSample(name="kick", rate=16000, volume=0.8),
        "PSGToneOn": E.PSGToneOn(channel=1, note="C", octave=5, volume=4),
        "PSGToneOff": E.PSGToneOff(channel=1),
        "PSGVolume": E.PSGVolume(channel=3, volume=9),
        "PSGNoiseOn": E.PSGNoiseOn(white=True, rate=2, volume=6),
        "PSGNoiseOff": E.PSGNoiseOff(),
        "End": E.End(),
    }
    # Nothing in the vocabulary may be unrepresentable as JSON: the whole
    # point is that a model can emit it as text.
    assert set(samples) == set(E.event_types()), "a type is missing a round-trip test"
    for name, event in samples.items():
        restored = E.Event.from_dict(json.loads(json.dumps(event.to_dict())))
        assert restored == event, f"{name} did not survive the round trip"


def test_spec_only_names_real_fields():
    for type_name, fields in E.SPEC.items():
        known = set(E.field_names(type_name))
        assert set(fields) <= known, f"{type_name}: SPEC names a field that does not exist"


def test_vocabulary_dump_is_json_serialisable():
    text = json.dumps(E.describe_vocabulary())
    assert "FMNoteOn" in text and "velocity" in text


def test_parse_repairs_a_sloppy_model_take():
    events, warnings = E.parse([
        {"type": "instrument", "ch": 0, "instr": "bass"},     # alias type + fields
        {"type": "FMNoteOn", "channel": 9, "note": "db5", "octave": 3, "junk": 1},
        {"type": "wait", "dur": "24"},                        # wrong case, string number
    ])
    assert [type(e).__name__ for e in events] == \
        ["FMInstrumentSelect", "FMNoteOn", "Wait", "End"]
    assert events[1].channel == 5, "an out-of-range channel should clamp, not throw"
    assert events[1].note == "C#", "Db must normalise to its canonical spelling"
    assert events[2].ticks == 24
    assert any("End" in w for w in warnings), "a missing terminator must be reported"
    assert any("junk" in w for w in warnings), "an unknown field must be reported"


def test_parse_drops_only_the_unsalvageable():
    events, warnings = E.parse([
        {"type": "NoSuchEvent"},
        "not even an object",
        {"no_type_at_all": 1},
        {"type": "Wait", "ticks": 5},
    ])
    assert [type(e).__name__ for e in events] == ["Wait", "End"]
    assert len(warnings) >= 4


def test_note_normalisation():
    for text, expected in [("a", "A"), ("A#4", "A#"), ("bb", "A#"), ("Db", "C#"),
                           ("B#", "C"), (60, "C")]:
        assert E.normalize_note(text) == expected, f"{text!r} should be {expected}"
    assert E.normalize_note("H") is None, "H is not a note name here"


def test_duration_follows_tempo_changes():
    events = [E.Wait(ticks=192),                 # 1.0s at the default 192/s
              E.Tempo(ticks_per_second=96.0),
              E.Wait(ticks=192),                 # 2.0s at 96/s
              E.End(),
              E.Wait(ticks=99999)]               # after End: must not count
    assert abs(E.duration_seconds(events, 192.0) - 3.0) < 1e-9
    assert E.total_ticks(events[:4]) == 384


def test_strict_parser_still_rejects_garbage():
    try:
        E.events_from_json([{"type": "FMNoteOn", "channel": 0}])
    except (TypeError, KeyError):
        return
    raise AssertionError("events_from_json must not silently invent missing fields")
