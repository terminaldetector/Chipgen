"""The hearing plugin (python/agentic/hearing): Essentia, built from the
fork, run in its own interpreter on Chipgen's renders.

Three levels. The unit tests need no Essentia: the client's cache and
envelope, reading and checking a target, the detectors on given
readings, the patch op the rhythm correction uses. The integration tests
run Essentia on real audio — a tone whose answer is known, and Chipgen
renders — and are skipped, saying so, where the hearing interpreter is
missing. The end-to-end test renders a piece, finds a defect in its
audio, changes the score, renders again and measures the change.
"""

import json
import math
import os
import shutil
import struct
import tempfile
import wave

import support  # noqa: F401  (puts python/ on the path)


def _tmp():
    return tempfile.mkdtemp(prefix="chipgen_hearing_")


def _essentia_or_skip():
    from agentic import hearing as H
    st = H.status(refresh=True)
    if not (st.get("essentia") or {}).get("available"):
        support.skip("Essentia is not available in the hearing "
                     "interpreter: " + str(st.get("essentia") or
                                           st.get("why")))
    return H


def _tone(path, hz=1000.0, seconds=3.0, rate=44100, stereo=True):
    n = int(seconds * rate)
    with wave.open(path, "wb") as w:
        w.setnchannels(2 if stereo else 1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            v = int(0.5 * 32767 * math.sin(2 * math.pi * hz * i / rate))
            frames += struct.pack("<h", v) * (2 if stereo else 1)
        w.writeframes(bytes(frames))
    return path


# -- unit ---------------------------------------------------------------------------------
def test_a_missing_interpreter_is_said_and_nothing_is_made_up():
    from agentic import hearing as H
    old = os.environ.get("CHIPGEN_HEARING_PYTHON")
    os.environ["CHIPGEN_HEARING_PYTHON"] = "/nonexistent/python"
    root = _tmp()
    try:
        r = H.analyze_rhythm(_tone(os.path.join(root, "t.wav"), seconds=0.2),
                             cache=False)
        assert r["status"] == "unavailable" and r["observations"] == {}
        assert "cannot run" in r["why"]
        r = H.analyze_rhythm(os.path.join(root, "missing.wav"))
        assert r["status"] == "error" and "no such file" in r["why"]
    finally:
        if old is None:
            del os.environ["CHIPGEN_HEARING_PYTHON"]
        else:
            os.environ["CHIPGEN_HEARING_PYTHON"] = old
        shutil.rmtree(root)


def test_the_cache_answers_the_same_audio_and_misses_a_changed_one():
    from agentic import hearing as H
    root = _tmp()
    old = os.environ.get("CHIPGEN_HEARING_CACHE")
    os.environ["CHIPGEN_HEARING_CACHE"] = os.path.join(root, "cache")
    calls = []
    real = H._run

    def fake(request, timeout):
        calls.append(request["function"])
        return {"status": "ok", "observations": {"n": len(calls)}}
    H._run = fake
    try:
        f = _tone(os.path.join(root, "t.wav"), seconds=0.2)
        a = H.analyze_timbre(f)
        b = H.analyze_timbre(f)
        assert calls == ["analyze_timbre"] and b["cached"] and \
            b["observations"] == a["observations"]
        H.analyze_timbre(f, start=0.1)               # another question
        _tone(f, hz=500.0, seconds=0.2)              # another sound
        H.analyze_timbre(f)
        assert len(calls) == 3
        H._run = lambda r, t: {"status": "error", "why": "x",
                               "observations": {}}
        H.analyze_tonality(f)                        # errors are not kept
        assert not [n for n in os.listdir(os.path.join(root, "cache"))
                    if n.startswith("analyze_tonality")]
    finally:
        H._run = real
        if old is None:
            del os.environ["CHIPGEN_HEARING_CACHE"]
        else:
            os.environ["CHIPGEN_HEARING_CACHE"] = old
        shutil.rmtree(root)


def test_a_target_is_read_through_lists_and_checked_both_ways():
    from agentic import hearing as H
    a = {"observations": {"measured": {"similarity": [[1.0, 0.8],
                                                      [0.8, 1.0]]},
                          "x": {"v": 0.0}}}
    b = {"observations": {"measured": {"similarity": [[1.0, 0.6],
                                                      [0.6, 1.0]]},
                          "x": {"v": 1.2}}}
    assert H.read(a, "measured.similarity.1.0") == 0.8
    assert H.read(a, "measured.similarity.5.0") is None
    t = H.check_target({"analysis": "s", "path": "measured.similarity.1.0",
                        "direction": "down", "by": 0.05}, {"s": (a, b)})
    assert t["met"] and t["before"] == 0.8 and t["after"] == 0.6
    t = H.check_target({"analysis": "s", "path": "x.v", "direction": "up",
                        "by": 1.5}, {"s": (a, b)})
    assert t["met"] is False
    t = H.check_target({"analysis": "s", "path": "x.missing",
                        "direction": "up"}, {"s": (a, b)})
    assert t["met"] is None and "missing" in t["why"]


def test_the_rhythm_detector_names_its_readings_and_the_score_beside_them():
    from agentic import fixtures, state as S, timeline as T
    from agentic.hearing import revise as RV
    tl = T.build(S.content(fixtures.masked_lead()))
    flat = {"status": "ok", "observations": {
        "measured": {"onsets": 16, "ioi_in_beats": {
            "histogram": [{"beats": 0.5, "share": 1.0}],
            "dominant_share": 1.0, "entropy_bits": 0.0}},
        "estimated": {"syncopation_share": 0.0}}}
    lively = {"status": "ok", "observations": {
        "measured": {"onsets": 16, "ioi_in_beats": {
            "histogram": [{"beats": 0.5, "share": 0.5}],
            "dominant_share": 0.5, "entropy_bits": 1.5}},
        "estimated": {"syncopation_share": 0.3}}}
    heard = {"observations": {"mix": {}, "bass": {"role": "bass",
                                                  "rhythm": flat},
                              "pad": {"role": "pad", "rhythm": lively}}}
    found = RV._monotonous(tl, heard)
    assert [f["voice"] for f in found] == ["bass"]
    f = found[0]
    assert f["score_side"]["row_gaps_between_onsets"] == [2]
    assert f["target"]["path"] == "measured.ioi_in_beats.entropy_bits"
    assert {c[0]["mode"] for c in f["corrections"]} >= {"syncopate"}
    assert RV._tempo_relation(75.0, 150.0) == "half"
    assert RV._tempo_relation(150.4, 150.0) == "same"
    assert RV._tempo_relation(120.0, 150.0) is None


def test_syncopate_pushes_beats_two_and_four_onto_a_key_off_row():
    from agentic import fixtures, patch as P, state as S, timeline as T
    c = S.content(fixtures.masked_lead())
    tl = T.build(c)
    try:
        P.apply(c, {"op": "rhythm", "voice": "bass", "range": "all",
                    "mode": "anticipate"}, tl)
        raise AssertionError("anticipate should find no held row")
    except Exception as error:                       # noqa: BLE001
        assert getattr(error, "code", "") == "nothing_to_change"
    new, d = P.apply(c, {"op": "rhythm", "voice": "bass", "range": "all",
                         "mode": "syncopate"}, tl)
    rows = new["sections"][0]["rows"]
    assert d["cells"] == 4 and [e["row"] for e in d["edits"]] == \
        [4, 12, 20, 28]
    assert rows[3][0] == "A-4" and rows[4][0] == "..."
    assert rows[8][0] == "A-4"                       # the downbeats stay


# -- integration: Essentia on real audio --------------------------------------------------------
def test_essentia_reads_a_known_tone_at_any_decoding_rate():
    H = _essentia_or_skip()
    root = _tmp()
    try:
        f = _tone(os.path.join(root, "a.wav"), hz=1000.0, seconds=3.0,
                  rate=22050)
        ins = H.inspect_audio(f, cache=False)
        assert ins["status"] == "ok", ins.get("why")
        m = ins["observations"]["measured"]
        assert abs(m["seconds"] - 3.0) < 0.01         # resampled to 44.1k
        assert abs(m["peak_dbfs"] + 6.0) < 0.3
        assert m["left_right_correlation"] > 0.99
        tim = H.analyze_timbre(f, cache=False)["observations"]["measured"]
        assert abs(tim["centroid_hz"]["median"] - 1000) < 60
        bands = tim["band_energy_share"]
        assert abs(sum(bands.values()) - 1.0) < 0.01
        assert bands["250-2000 Hz"] > 0.95
        ton = H.analyze_tonality(f, cache=False)
        pp = ton["observations"]["estimated"]["predominant_pitch"]
        assert abs(pp["median_midi"] - 83.2) < 0.5     # 1000 Hz
        win = H.analyze_timbre(f, start=1.0, end=2.0, cache=False)
        assert win["start_seconds"] == 1.0 and win["end_seconds"] == 2.0
    finally:
        shutil.rmtree(root)


def test_essentia_hears_a_chipgen_bass_render_as_straight_eighths():
    H = _essentia_or_skip()
    from agentic import fixtures, render as R, state as S, timeline as T
    from agentic.hearing import revise as RV
    root = _tmp()
    try:
        st = fixtures.masked_lead()
        st["order"] = ["A", "A"]
        tl = T.build(S.content(st))
        cache = R.Cache(os.path.join(root, "renders"))
        bass = RV.render_wav(tl, os.path.join(root, "bass.wav"), cache,
                             voices=["bass"])["file"]
        mix = RV.render_wav(tl, os.path.join(root, "mix.wav"), cache)["file"]
        r = H.analyze_rhythm(bass, bpm_hint=150, cache=False)
        assert r["status"] == "ok", r.get("why")
        m = r["observations"]["measured"]
        assert m["onsets"] == 32                    # 16 notes a pass
        assert m["ioi_in_beats"]["dominant_share"] == 1.0
        assert m["ioi_in_beats"]["histogram"][0]["beats"] == 0.5
        assert abs(r["observations"]["estimated"]["tempo"]["bpm"] - 150) < 3
        sec = H.analyze_sections(mix, boundaries=RV._boundaries(tl),
                                 cache=False)
        segs = sec["observations"]["measured"]["sections"]
        assert len(segs) == 2 and segs[1]["to_previous"]["similarity"] > 0.9
        assert sec["observations"]["estimated"]["form_letters"] == "A A"
        ton = H.analyze_tonality(mix, cache=False)["observations"]
        assert set(ton["estimated"]["key"]["profiles"]) == \
            {"bgate", "temperley", "edma"}
        assert "not ground truth" in ton["estimated"]["key"]["note"]
    finally:
        shutil.rmtree(root)


def test_the_facade_renders_a_project_and_hears_it():
    _essentia_or_skip()
    from agent import Agent
    root = _tmp()
    try:
        a = Agent(root)
        a.call("create_musical_state", preset="masked_lead", project_id="m")
        r = a.call("analyze_rhythm", project_id="m", voices="bass")
        assert r["status"] == "ok" and r["tool"] == "essentia"
        assert r["observations"]["measured"]["grid"].startswith(
            "the given tempo 150")
        for key in ("audio_sha256", "start_seconds", "end_seconds",
                    "confidence", "limitations", "model_or_algorithm"):
            assert key in r, key
        assert r["confidence"] is None
        st = a.call("hearing_status")
        assert st["essentia"]["available"]
    finally:
        shutil.rmtree(root)


# -- end to end ---------------------------------------------------------------------------------
def test_a_monotonous_bass_heard_in_the_render_is_syncopated_and_heard_again():
    _essentia_or_skip()
    from agentic import fixtures, render as R, state as S
    from agentic.hearing import revise as RV
    root = _tmp()
    try:
        st = fixtures.masked_lead()
        st["order"] = ["A"] * 4
        p = S.Project.create(os.path.join(root, "p"), st)
        cache = R.Cache(p.path("renders"))
        rep = RV.revise(p, cache, defect="monotonous_rhythm")
        assert rep["outcome"] == "committed", rep["outcome"]
        f = rep["working_on"]
        assert f["voice"] == "bass" and f["score_side"]["notes"] == 64
        kept = [x for x in rep["attempts"] if x.get("accepted")][0]
        assert kept["chain"][0]["mode"] == "syncopate"
        assert kept["target"]["before"] == 0.0
        assert kept["target"]["after"] >= 0.5 and kept["target"]["met"]
        assert all(g["ok"] for g in kept["guards"])
        assert all(s["ok"] for s in kept["side_readings"])
        assert os.path.exists(kept["render"]["target"])
        assert kept["score_side"]["before"]["notes"] == \
            kept["score_side"]["after"]["notes"]     # moved, not added
        assert p.head == rep["rev"] == 1
        dec = p.state["decisions"][-1]
        assert dec["kind"] == "hearing:monotonous_rhythm" and \
            dec["after"] > dec["before"]
        json.dumps(rep)                               # a plain report
    finally:
        shutil.rmtree(root)
