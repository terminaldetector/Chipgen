"""References, parallel listening, instruments and sketches for the
agentic loop (python/agentic/{features,references,listen,instrument,
sketches,guided}.py): the measuring scale, a reference kept with its
source checked against its audio, fragments cut on bars and matched in
loudness, observations that say what they rest on, an instrument moved
toward a sound goal, the musical ops changing only what they say, the
guard against copying a reference, and one guided round.

No reference here is anyone else's music: the modules and recordings are
made from this engine's own renders.
"""

import json
import math
import os
import shutil
import subprocess
import tempfile

import support  # noqa: F401  (puts python/ on the path)

from test_agentic import _ABEar, _Banks, _project


def _tmp():
    return tempfile.mkdtemp(prefix="chipgen_refs_")


def _composed(seed: int = 3):
    from agentic import compose as C, fixtures
    p = _project(fixtures.etude())
    C.continue_composition(p, hear=False, seed=seed)
    return p


# -- the measuring scale ------------------------------------------------------------
def test_the_measuring_scale_reads_known_signals():
    from agentic import features as F
    rate = 44100
    a = 10 ** (-20 / 20.0)
    sine = []
    for i in range(rate * 2):
        v = a * math.sin(2 * math.pi * 997 * i / rate)
        sine += [v, v]
    assert abs(F.loudness(sine)["lufs"] + 20.0) < 0.1
    st = F.stereo(sine)
    assert st["mono"] and st["correlation"] == 1.0
    # a click track: 128 BPM, a low thump on every fourth beat
    beat, first = 60 / 128.0, 0.37
    mono = [0.0] * (rate * 10)
    k = 0
    while first + k * beat < 9.5:
        s = int((first + k * beat) * rate)
        for j in range(int(0.06 * rate)):
            v = 0.3 * math.exp(-j / (0.015 * rate)) * \
                math.sin(2 * math.pi * 3000 * j / rate)
            if k % 4 == 0:
                v += 0.6 * math.exp(-j / (0.04 * rate)) * \
                    math.sin(2 * math.pi * 60 * j / rate)
            mono[s + j] += v
        k += 1
    g = F.beat_grid(mono)
    assert abs(g["bpm"] - 128.0) < 1.0, g
    assert abs(g["first_downbeat_s"] - first) < 0.03, g
    # a vibrato: 30 cents at 6 Hz after 200 ms on A-4
    ph, tone = 0.0, []
    for i in range(int(1.2 * rate)):
        t = i / rate
        dev = 0.0 if t < 0.2 else 30.0 * math.sin(2 * math.pi * 6 * (t - 0.2))
        ph += 2 * math.pi * 440.0 * 2 ** (dev / 1200) / rate
        tone.append(0.5 * math.sin(ph))
    prof = F.note_profile(tone, rate, [{"start": 0.0, "end": 1.15,
                                        "f0": 440.0}])
    assert 24 <= prof["vibrato_depth_cents"] <= 33, prof
    assert 5.4 <= prof["vibrato_rate_hz"] <= 6.6, prof
    assert 170 <= prof["vibrato_delay_ms"] <= 240, prof


# -- references ---------------------------------------------------------------------------
def _module_of(project, root):
    """The project's music as an Impulse Tracker module (this engine's
    own IT export): a module reference made of nobody else's music."""
    from agentic import capabilities as CAP
    out = CAP.export(project, "it", os.path.join(root, "ours.it"))
    return out["path"]


def test_a_module_reference_is_kept_with_its_source_checked_by_its_audio():
    from agentic import openmpt as O, references as REF
    if not O.available():
        support.skip("libopenmpt is not installed")
    src = _composed()
    root = _tmp()
    path = _module_of(src, root)
    p = _project(src.state)
    r = REF.add(p, path, tags=["rhythm", "space"], note="our own étude, "
                "played by libopenmpt", seconds=20)
    assert r["kind"] == "module" and r["engine"].startswith("libopenmpt")
    assert r["origin"]["sha256"] and r["origin"]["file"] == "ours.it"
    assert os.path.exists(p.path(r["files"]["audio"]))
    assert r["source_notes"] > 20 and r["voices"]
    assert r["grid"]["bars"] and r["grid"]["source"] in ("score", "audio")
    kinds = {c["kind"] for c in r["claims"]}
    assert "tempo" in kinds and "density" in kinds
    for c in r["claims"]:
        assert c["audio_check"]["verdict"] in (
            "confirmed", "not confirmed", "not measurable",
            "confirmed at half time", "confirmed at double time",
            "related, not confirmed")
    density = [c for c in r["claims"] if c["kind"] == "density"
               and c["audio_check"]["verdict"] != "not measurable"]
    assert any(c["audio_check"]["reading"]["recall"] >= 0.7
               for c in density), density
    card = REF.card(r)
    assert card["tags"] == ["rhythm", "space"] and card["claims"]
    for bad, code in ((lambda: REF.add(p, path, tags=["loudness"]),
                       "bad_tag"),
                      (lambda: REF.get(p, "ref9"), "no_reference"),
                      (lambda: REF.add(p, os.path.join(root, "x.ogg")),
                       "no_file")):
        try:
            bad()
            assert False, code
        except REF.AgenticError as error:
            assert error.code == code, error.to_dict()
    shutil.rmtree(root)
    shutil.rmtree(os.path.dirname(p.root))
    shutil.rmtree(os.path.dirname(src.root))


def test_a_recording_gets_its_grid_from_its_audio_and_says_so():
    from agentic import references as REF, render as R, timeline as T
    from agentic import state as S
    src = _composed()
    tl = T.build(S.content(src.state))
    rng = tl.range("all")
    root = _tmp()
    wav = os.path.join(root, "take.wav")
    REF._write_wav(wav, R.render_range(tl, rng).segment(
        rng["t0"], rng["t1"], mono=False))
    path = wav
    if shutil.which("lame"):
        path = os.path.join(root, "take.mp3")
        subprocess.run(["lame", "--silent", wav, path], check=True)
    p = _project(src.state)
    r = REF.add(p, path, tags=["rhythm"], note="a recording, no score")
    assert r["kind"] == "audio" and r["grid"]["source"] == "audio"
    assert "estimated" in r["grid"]["how"] and r.get("claims") is None
    ratio = r["grid"]["bpm"] / 140.0
    assert min(abs(ratio - x) for x in (1.0, 2.0, 0.5)) < 0.03, r["grid"]
    if path.endswith(".mp3"):
        assert "LAME" in r["engine"] or "lame" in r["engine"]
    shutil.rmtree(root)
    shutil.rmtree(os.path.dirname(p.root))
    shutil.rmtree(os.path.dirname(src.root))


def test_a_score_reference_plays_its_own_bank_and_leaves_ours_alone():
    import instruments
    from agentic import references as REF
    root = _tmp()
    bank = os.path.join(root, "ref_bank.json")
    patch = instruments.instrument_to_dict(instruments.get("square_lead"))
    patch["name"] = "reference_only_patch"
    with open(bank, "w") as handle:
        json.dump([patch], handle)
    score = os.path.join(root, "ref.trk")
    with open(score, "w") as handle:
        handle.write("bpm 120\nlpb 4\ninst fm0 reference_only_patch\n"
                     "vib fm0 40 6 0.1\ncols fm0 noise\n"
                     + ("A-4  w1\n...  ===\n...  w1\n...  ===\n"
                        "C-5  w1\n...  ===\n...  w1\n===  ===\n") * 4)
    p = _project(_composed().state)
    r = REF.add(p, score, bank=bank, tags=["modulation"], seconds=10)
    assert "reference_only_patch" not in instruments.BANK
    assert r["kind"] == "trk" and r["voices"]["fm0"]["vibrato_share"] > 0.5
    vib = [c for c in r["claims"] if c["kind"] == "vibrato"]
    assert vib and vib[0]["audio_check"]["verdict"] == "confirmed", vib
    assert 25 <= vib[0]["audio_check"]["reading"]["depth_cents"] <= 50
    shutil.rmtree(root)


# -- parallel listening ----------------------------------------------------------------------
def test_fragments_are_cut_on_bars_and_brought_to_one_loudness():
    from agentic import features as F, listen as LI, references as REF
    from agentic import openmpt as O
    if not O.available():
        support.skip("libopenmpt is not installed")
    src = _composed()
    root = _tmp()
    path = _module_of(src, root)
    p = _project(src.state)
    REF.add(p, path, tags=["rhythm"], seconds=20, check=False)
    ours = LI.of_project(p, "A", stems=True)
    theirs = LI.of_reference(p, "ref1", 1, 2)
    assert ours["bars"] == [1, 2] and theirs["bars"] == [1, 2]
    # each on its own grid: two bars of each, whatever their tempos
    assert abs(ours["seconds"] - 2 * 4 * 60.0 / 140.0) < 0.02
    bar = REF.get(p, "ref1")["grid"]["beat_s"] * 4
    assert abs(theirs["seconds"] - 2 * bar) < 0.1, (theirs["seconds"], bar)
    index = LI.write([ours, theirs], os.path.join(root, "out"), "cmp",
                     mp3=False)
    for f, e in zip((ours, theirs), index["fragments"]):
        scaled = [v * 10 ** (e["gain_db"] / 20.0) for v in f["samples"]]
        lufs = F.loudness(scaled)["lufs"]
        assert abs(lufs - (LI.TARGET_LUFS + index["loudness"][
            "common_reduction_db"])) < 0.2, (lufs, e)
    assert set(index["fragments"][0]["stems"]) >= {"lead", "bass"}
    a, b = index["fragments"][0]["in_reel"], index["fragments"][1]["in_reel"]
    assert abs(b[0] - a[1] - LI.GAP_S) < 0.01
    assert os.path.exists(index["reel"]["wav"])
    shutil.rmtree(root)


def test_an_observation_says_what_it_rests_on():
    from agentic import ear as EAR, listen as LI, references as REF
    from agentic import openmpt as O, patch as P, state as S
    from agentic import timeline as T
    if not O.available():
        support.skip("libopenmpt is not installed")
    src = _composed()
    # the reference: the same piece with its hats doubled — busier
    c = S.content(src.state)
    busy, _ = P.apply(c, {"op": "density", "voice": "hats", "range": "all",
                          "mode": "double"}, T.build(c))
    other = _project(src.state)
    other.commit(busy, {"why": "test"})
    root = _tmp()
    path = _module_of(other, root)
    p = _project(src.state)
    REF.add(p, path, tags=["rhythm"], seconds=20, check=False)
    frags = [LI.of_project(p, "A"), LI.of_reference(p, "ref1", 1, 2)]
    rep = LI.compare(p, frags, os.path.join(root, "a"), "a")
    assert not rep["listening"]["available"]
    assert "nothing listened" in rep["listening"]["why"]
    measured = [o for o in rep["observations"] if o["basis"] == "measured"]
    assert any(o["feature"] == "notes_per_beat" and o["theirs"] > o["ours"]
               for o in measured), rep["observations"]
    for o in rep["observations"]:
        assert o["basis"] in ("measured", "assumed")
        for word in ("heard", "listened", "sounds"):
            assert word not in o["statement"], o["statement"]
    # a verified model's observations come back as heard by it
    adapter = _ABEar("B")
    adapter.script = [{"fragment": 1, "t0": 0.2, "t1": 1.0,
                       "feature": "rhythm", "statement": "sparser pulse",
                       "voices": ["hats"], "confidence": 0.7}]
    ear = EAR.choose("hybrid", adapter, EAR.verify(adapter))
    rep = LI.compare(p, frags, os.path.join(root, "b"), "b", ear=ear)
    heard = [o for o in rep["observations"]
             if o["basis"] == "heard by model"]
    assert heard and heard[0]["voices"] == ["hats"]
    assert rep["listening"]["available"]
    shutil.rmtree(root)


# -- the musical ops and the copy guard --------------------------------------------------------
def test_the_musical_ops_change_only_what_they_say():
    from agentic import loop as L, patch as P, state as S, timeline as T
    p = _composed()
    c = S.content(p.state)
    tl = T.build(c)
    rng = tl.range("B")
    for spec in ({"op": "articulation", "voice": "lead", "range": "B",
                  "gate": 0.5},
                 {"op": "density", "voice": "hats", "range": "B",
                  "mode": "double"},
                 {"op": "density", "voice": "bass", "range": "B",
                  "mode": "thin"},
                 {"op": "rhythm", "voice": "hats", "range": "B",
                  "mode": "swing"},
                 {"op": "voicing", "voice": "pad", "range": "B"},
                 {"op": "echo", "voice": "lead", "range": "B"}):
        new, delta = P.apply(c, spec, tl)
        tl2 = T.build(new)
        a, b = L._onsets(tl, rng), L._onsets(tl2, tl2.range("B"))
        assert not L._notes_kept(a, b, L._allowed([spec])), spec
        # without the op's allowance the change is seen
        if spec["op"] in ("density", "echo"):
            assert L._notes_kept(a, b, {}), spec
        assert L._pitches(tl, rng, "lead") == L._pitches(tl2, tl2.range("B"),
                                                         "lead"), spec
    new, delta = P.apply(c, {"op": "echo", "voice": "lead", "range": "B",
                             "rows": 3}, tl)
    echo = new["instruments"][delta["new_voice"]]
    assert echo["role"] == "echo" and echo["pan"] == "R"
    assert echo["channel"] not in {i["channel"] for v, i in
                                   c["instruments"].items()}
    for spec, code in (({"op": "density", "voice": "lead", "range": "B",
                         "mode": "thin"}, "melodic_voice"),
                       ({"op": "pan", "voice": "hats", "side": "L"},
                        "no_pan"),
                       ({"op": "voicing", "voice": "bass", "range": "B"},
                        "not_a_pad")):
        try:
            P.apply(c, spec, tl)
            assert False, spec
        except S.AgenticError as error:
            assert error.code == code, error.to_dict()
    shutil.rmtree(os.path.dirname(p.root))


def test_a_change_that_brings_in_a_reference_figure_is_refused():
    import copy
    from agentic import guided as G, patch as P, references as REF
    from agentic import state as S, timeline as T
    p = _composed()
    c = S.content(p.state)
    tl = T.build(c)
    rng = tl.range("B")
    # a reference whose melody has a figure the piece does not
    figure = [(0, 64), (2, 71), (3, 69), (4, 68), (6, 66), (7, 64)]
    folder = p.path("references", "ref1")
    os.makedirs(folder)
    row_s = 60.0 / 120.0 / 4
    with open(os.path.join(folder, "notes.json"), "w") as handle:
        json.dump([{"voice": "ch3", "start": 10.0 + r * row_s,
                    "pitch": pitch} for r, pitch in figure], handle)
    p.state.setdefault("references", []).append(
        {"id": "ref1", "kind": "module", "tags": []})
    # rows that write that figure (transposed, at this tempo) into B
    copied = copy.deepcopy(c)
    k = copied["columns"].index("fm1")
    b = next(s for s in copied["sections"] if s["id"] == "B")
    for r in range(8):
        b["rows"][r][k] = "..."
    for r, pitch in figure:
        b["rows"][r][k] = P._name(pitch + 2)
    g = G.copy_guard(p, tl, T.build(copied), rng)
    assert not g["ok"] and "ref1 ch3" in g["detail"], g
    # a change to the hats brings in no melody
    new, _ = P.apply(c, {"op": "density", "voice": "hats", "range": "B",
                         "mode": "double"}, tl)
    assert G.copy_guard(p, tl, T.build(new), rng)["ok"]
    # nor does an echo of the piece's own lead: that figure was the piece's
    new, _ = P.apply(c, {"op": "echo", "voice": "lead", "range": "B"}, tl)
    assert G.copy_guard(p, tl, T.build(new), rng)["ok"]
    assert REF.notes(p, "ref1")
    shutil.rmtree(os.path.dirname(p.root))


# -- sketches ------------------------------------------------------------------------------------
def test_sketches_keep_drafts_and_rejections_with_their_reasons():
    from agentic import sketches as SK, state as S
    p = _composed()
    before = S.content_hash(S.content(p.state))
    ids = SK.section_variants(p, "A", seeds=[7]) + \
        SK.section_variants(p, "B", energies=[0.95])
    assert len(ids) == 2
    assert S.content_hash(S.content(p.state)) == before   # nothing changed
    for sid, section in zip(ids, ("A", "B")):
        mine = next(s for s in S.content(p.state)["sections"]
                    if s["id"] == section)
        theirs = next(s for s in SK.content_of(p, sid)["sections"]
                      if s["id"] == section)
        assert mine["rows"] != theirs["rows"], section
        assert SK.get(p, sid)["measured"]["same_as_piece"] is False
    SK.choose(p, ids[1], "busier")
    try:
        SK.reject(p, ids[0], "")
        assert False
    except S.AgenticError as error:
        assert error.code == "no_reason"
    SK.reject(p, ids[0], "the theme does not come back")
    reopened = S.Project.open(p.root)
    states = {s["id"]: s["status"] for s in reopened.state["sketches"]}
    assert states == {ids[0]: "rejected", ids[1]: "chosen"}
    assert SK.chosen(reopened)[0]["chosen_because"] == "busier"
    shutil.rmtree(os.path.dirname(p.root))


# -- an instrument and a guided round (slow: they render) ---------------------------------------
def test_an_instrument_moves_toward_a_sound_goal_or_stays():
    from agentic import fixtures, instrument as INS, state as S
    with _Banks():          # what it installs leaves with it
        p = _project(fixtures.masked_lead())
        rec = INS.design(p, "lead", {"vibrato_depth_cents": 30.0,
                                     "vibrato_rate_hz": 6.0,
                                     "vibrato_delay_ms": 0.0}, "all",
                         max_candidates=2)
        assert rec["outcome"] == "committed", rec["why"]
        kept = next(c for c in rec["candidates"] if c["id"] == rec["chosen"])
        assert kept["profile"]["vibrato_depth_cents"] >= 20.0
        assert all(g["ok"] for g in kept["guards"])
        assert rec["listening"]["available"] is False
        name = p.state["instruments"]["lead"]["patch"]
        assert name != "square_lead" and name == rec["patch_after"]
        banks = p.state["structure"]["banks"]
        assert any(b["path"].endswith(f"{name}.bank.json") for b in banks)
        rejected = [c for c in rec["candidates"] if c is not kept]
        assert all(c.get("sketch") for c in rejected if c.get("profile"))
        again = S.Project.open(p.root)          # the bank comes back with it
        assert again.state["instruments"]["lead"]["patch"] == name
        rev = again.rollback(0, {"why": "test"})
        assert again.revision(rev)["hash"] == again.revision(0)["hash"]
        shutil.rmtree(os.path.dirname(p.root))


def test_a_transcriptions_rows_that_miss_the_eighths_give_no_offbeat_reading():
    # A transcription's 52 ms rows against a 164 BPM beat: an eighth is 3.5
    # rows, so every other straight eighth sits half a row away, past the
    # tolerance; the share of onsets between the eighths would measure the
    # grid. On the beat the rows divide, and on a source's own rows, it is
    # a reading.
    from agentic import listen as LI
    ref = {"grid": {"source": "audio", "beat_s": 0.7296},
           "source_grid": {"row_s": 0.05208}}
    why = LI._rows_misfit(ref, 0.3648)
    assert why and "52 ms" in why, why
    assert LI._rows_misfit(ref, 0.7296) is None
    ref["grid"]["source"] = "score"
    assert LI._rows_misfit(ref, 0.3648) is None


def test_a_combination_that_broke_a_guard_is_tried_with_the_next_best():
    # Each family's best change put together can break a guard neither
    # broke alone; the round then takes each family's next best in turn.
    from agentic import instrument as INS
    compiled = INS._base_compiled("square_lead")
    goal = {"vibrato_depth_cents": 56.0, "vibrato_rate_hz": 7.5,
            "vibrato_share": 0.6, "brightness_body": 2.3}
    hyps = INS.hypotheses(compiled, {"brightness_body": 6.4}, goal, "lead",
                          "fm1", lengths=[0.2, 0.3, 0.6, 0.9])
    ids = [h["id"] for h in hyps]
    assert {"vib1", "vib2", "br1", "br2"} <= set(ids), ids
    # a made-up first round: vib2 then vib1 nearest on the vibrato's
    # readings, br2 then br1 on the body's brightness
    parts = {"vib1": 0.4, "vib2": 0.2, "vib3": 0.8, "vib4": 0.9,
             "br1": 2.0, "br2": 1.0}
    tried = []
    for h in hyps:
        part = parts.get(h["id"], 5.0)
        tried.append({"distance": {"parts": {
            k: part for k in INS._FAMILY_READINGS.get(h["family"], ())
            if k in goal}}, "guards": [{"ok": True}], "closed": 0.3})
    joint = INS.combine(compiled, goal, hyps, tried)
    assert joint["parts"] == ["br2", "vib2"], joint["parts"]
    assert INS.combine(compiled, goal, hyps, tried,
                       avoid={"vib2"})["parts"] == ["br2", "vib1"]
    assert INS.combine(compiled, goal, hyps, tried,
                       avoid={"br2"})["parts"] == ["br1", "vib2"]


def test_a_vibrato_goal_takes_the_references_share_not_its_delay():
    # A reference's vibrato delay is the length of its own held notes; the
    # share of notes that swing is what carries over, and the delay is
    # fitted to this part's notes from it.
    from agentic import guided as G, instrument as INS
    goal = {"vibrato_depth_cents": 56.1, "vibrato_share": 0.6,
            "vibrato_delay_ms": 800.0}
    why = G._complete_goal(goal, [{"vibrato_rate_hz": 7.67,
                                   "vibrato_delay_ms": 800.0}])
    assert goal == {"vibrato_depth_cents": 56.1, "vibrato_share": 0.6,
                    "vibrato_rate_hz": 7.67}, goal
    assert "800" in why
    lengths = [0.2, 0.3, 0.6, 0.9]
    delay = INS._delay_for_share(lengths, 0.5)
    assert delay == 450.0
    # half of the notes last 150 ms past it: those swing
    assert sum(1 for n in lengths
               if n - delay / 1000.0 >= 0.15 - 1e-9) == 2


def test_a_guided_round_keeps_what_moves_toward_the_reference():
    from agentic import guided as G, openmpt as O, patch as P
    from agentic import references as REF, state as S, timeline as T
    if not O.available():
        support.skip("libopenmpt is not installed")
    with _Banks():          # what it installs leaves with it
        src = _composed()
        c = S.content(src.state)
        busy, _ = P.apply(c, {"op": "density", "voice": "hats", "range": "all",
                              "mode": "double"}, T.build(c))
        other = _project(src.state)
        other.commit(busy, {"why": "test"})
        root = _tmp()
        path = _module_of(other, root)
        p = _project(src.state)
        REF.add(p, path, tags=["rhythm"], seconds=20, check=False)
        rep = G.improve_toward(p, "A", ["ref1"], folder=os.path.join(root, "g"),
                               max_gaps=1)
        rhythm = [w for w in rep["worked_on"] if w["dimension"] == "rhythm"]
        assert rhythm, rep["worked_on"]
        w = rhythm[0]
        assert w["outcome"] == "committed", w
        kept = next(h for h in w["hypotheses"] if h["id"] == w["chosen"])
        assert kept["after"] > kept["before"] and kept["chain"][0]["op"] in (
            "density", "rhythm")
        assert any(g["name"] == "no_reference_copy" and g["ok"]
                   for g in kept["guards"])
        assert os.path.exists(kept["ab_reel"])
        assert p.head == 1 and rep["listening"]["available"] is False
        shutil.rmtree(root)


def test_a_sketch_drafted_before_a_voice_came_keeps_its_cells_by_voice():
    # A sketch drafted before the piece gained a voice (an echo on a new
    # column) is still compared: its cells go to the piece's columns by
    # name, and the new voice is silent in it.
    from agentic import guided as G
    rows = [["C-4", "E-4", "kick"], ["...", "===", "..."]]
    assert G._in_columns(rows, ["fm0", "fm1", "dac"],
                         ["fm0", "fm1", "dac", "fm3"]) == [
        ["C-4", "E-4", "kick", "..."], ["...", "===", "...", "..."]]
    assert G._in_columns(rows, ["fm0", "fm1", "dac"],
                         ["fm1", "fm0", "dac"])[0] == ["E-4", "C-4", "kick"]


def test_a_new_section_is_heard_with_the_one_before_the_join_and_a_sketch():
    import json as _json
    from agentic import compose as C, fixtures, guided as G
    from agentic import sketches as SK
    p = _project(fixtures.etude())
    C.continue_composition(p, stop_after=1, seed=3, hear=False)   # A
    sid = SK.section_variants(p, "A2", energies=[0.95])[0]
    SK.choose(p, sid, "a busier A2")
    C.continue_composition(p, stop_after=1, hear=False)            # A2
    out = G.hear_continuation(p, "A2", sketch_ids=[sid])
    assert out["join"]["score"]["between"] == ["A", "A2"]
    assert "loudness_change_lufs" in out["join"]
    with open(out["index"]) as handle:
        kinds = [e["kind"] for e in _json.load(handle)["fragments"]]
    assert kinds == ["ours", "previous", "sketch"], kinds
    assert out["sketches"] == [{"id": sid, "label": SK.get(p, sid)["label"],
                                "compared": True}]
    assert out["listening"]["available"] is False
    assert os.path.exists(out["reel"]["wav"])
    for o in out["observations"]:
        assert o["basis"] in ("measured", "assumed"), o
    shutil.rmtree(os.path.dirname(p.root))


# -- the facade -------------------------------------------------------------------------------------
def test_the_facade_keeps_references_and_sketches_and_says_who_listened():
    from agent import Agent
    root = _tmp()
    a = Agent(root)
    a.call("create_musical_state", preset="etude", project_id="e")
    a.call("compose_range", project_id="e")
    r = a.call("check_listening")
    assert r["available"] is False and "consequence" in r
    r = a.call("add_reference", project_id="e", path="/no/such.mp3")
    assert r["error"] == "no_file"
    r = a.call("add_reference", project_id="e", path=__file__)
    assert r["error"] == "bad_reference"
    r = a.call("save_sketch", project_id="e", label="the first take")
    sid = r["sketch"]["id"]
    assert a.call("reject_sketch", project_id="e", sketch_id=sid,
                  why="")["error"] == "no_reason"
    r = a.call("list_sketches", project_id="e")
    assert r["sketches"][0]["status"] == "open"
    assert a.call("list_references", project_id="e")["references"] == []
    shutil.rmtree(root)


# -- hearing that decides: the router, the ear's choice, stereo, memory ---------------------------
class _ChoosingEar:
    """A stand-in for a verified audio model that decides from the audio
    it is sent. It answers the probes from the samples, reports a scripted
    observation about the piece, and when asked to choose among versions
    it reads each version's span (from the prompt's index) and picks the
    one with the fewest zero crossings for a goal that asks for less
    brightness (most otherwise) — or answers "uncertain" when told to.
    It records the channel count of every clip it was sent."""

    name = "test-choosing-ear"

    def __init__(self, observation=None, uncertain=False, pick=None):
        self.observation = observation
        self.uncertain = uncertain
        self.pick = pick
        self.channels = []
        self.choices = []

    def declares_audio(self):
        return True

    def listen(self, wav, prompt):
        import io as _io
        import re
        import wave as _wave
        from array import array
        with _wave.open(_io.BytesIO(wav)) as w:
            rate, ch = w.getframerate(), w.getnchannels()
            pcm = array("h", w.readframes(w.getnframes()))
        self.channels.append(ch)
        mono = [sum(pcm[i:i + ch]) / ch for i in range(0, len(pcm), ch)]
        if "How many" in prompt or "higher or lower" in prompt:
            from test_agentic import _DSPEar
            return _DSPEar().listen(wav, prompt)
        if "versions of the same passage" in prompt:
            spans = [(float(a), float(b)) for a, b in re.findall(
                r"^\d+\. ([\d.]+)-([\d.]+) s:", prompt, re.M)]
            if self.uncertain:
                return json.dumps({"choice": "uncertain", "confidence": 0.2,
                                   "statement": "cannot tell"})
            if self.pick is not None:
                self.choices.append({"pick": self.pick,
                                     "matched": "matched in loudness"
                                     in prompt})
                return json.dumps({"choice": self.pick + 1,
                                   "confidence": 0.8, "statement": "scripted"})

            def crossings(a, b):
                seg = mono[int(a * rate):int(b * rate)]
                return sum(1 for x, y in zip(seg, seg[1:])
                           if (x < 0) != (y < 0)) / max(1, len(seg))
            zc = [crossings(a, b) for a, b in spans]
            darker = "less" in prompt or "darker" in prompt or \
                "softer" in prompt
            pick = (min if darker else max)(range(len(zc)),
                                             key=lambda k: zc[k])
            self.choices.append({"crossings": zc, "pick": pick})
            return json.dumps({"choice": pick + 1, "confidence": 0.8,
                               "statement": "by ear", "ranking": [pick + 1]})
        if "fragments" in prompt or "Report only what you hear" in prompt:
            return json.dumps({"observations": [self.observation]
                               if self.observation else []})
        return "{}"


def _choosing(adapter, cache=None):
    from agentic import ear as EAR, render as R
    return EAR.choose("hybrid", adapter, EAR.verify(adapter),
                      cache or R.Cache())


def test_an_observation_goes_where_something_can_act_on_it():
    from agentic import fixtures, guided as G, state as S, timeline as T
    tl = T.build(S.content(fixtures.masked_lead()))
    heard = {"basis": "heard by model", "about_the_piece": True,
             "statement": "the lead's held notes are too bright"}
    r = G.route(dict(heard, category="body_brightness", direction="less",
                     voices=["lead"]), tl, "lead")
    assert r["route"] == "instrument" and r["voice"] == "lead"
    assert r["relative"] == {"brightness_body": 0.6}
    r = G.route(dict(heard, category="space", direction="more"), tl, "lead")
    assert r["route"] == "arrangement" and \
        r["feature"] == "stereo.side_over_mid_db" and r["sign"] == 1.0
    r = G.route(dict(heard, category="balance", direction="less",
                     voices=["pad"]), tl, "lead")
    assert r["chains"][0]["chain"][0] == {"op": "level", "voice": "pad",
                                          "range": "@", "db": -3.0}
    assert G.route(dict(heard, about_the_piece=False, category="space",
                        direction="more"), tl, "lead")["route"] is None
    assert G.route(dict(heard, category="other", direction="more"), tl,
                   "lead")["route"] is None
    measured = {"basis": "measured", "against_kind": "reference",
                "feature": "voice.vibrato_depth_cents", "theirs": 50.0}
    assert G.route(measured, tl, "lead") == {
        "route": "instrument", "voice": "lead",
        "goal": {"vibrato_depth_cents": 50.0}}
    assert G.route({"basis": "assumed"}, tl, "lead")["route"] is None


def test_the_ear_chooses_among_versions_it_is_sent_in_stereo():
    from agentic import ear as EAR
    import math as _m
    rate = 8000

    def tone(f, sec=0.5):
        out = []
        for i in range(int(sec * rate)):
            v = 0.3 * _m.sin(2 * _m.pi * f * i / rate)
            out += [v, 0.5 * v]          # left of centre
        return out
    adapter = _ChoosingEar()
    ear = _choosing(adapter)
    v = EAR.ab_choose(ear, [("as it is", tone(2000)), ("bright", tone(3000)),
                            ("dark", tone(500))], "less bright, softer",
                      rate=rate)
    assert v["available"] and v["choice"] == 2, v
    assert v["clip"]["channels"] == 2 and adapter.channels[-1] == 2
    assert len(v["clip"]["versions"]) == 3
    # stereo kept: an anti-phase side signal survives (a mono mix would
    # cancel it to silence)
    wide = [x for i in range(4000) for x in (0.3 * _m.sin(i / 3.0),
                                             -0.3 * _m.sin(i / 3.0))]
    a = EAR.ab_clip(wide, wide, rate)[0]
    assert max(abs(x) for x in a) > 0.1
    assert max(abs(a[i] + a[i + 1]) for i in range(0, 2000, 2)) < 1e-6
    unsure = EAR.ab_choose(_choosing(_ChoosingEar(uncertain=True)),
                           [("a", tone(500)), ("b", tone(900))], "x",
                           rate=rate)
    assert unsure["choice"] is None and unsure["uncertain"]
    none = EAR.ab_choose(EAR.ToolEar(), [("a", tone(500))], "x")
    assert none["available"] is False and "choice" not in none


def test_a_mono_machine_is_named_and_sent_mono():
    from agentic import ear as EAR
    assert EAR.platform_of({"columns": ["fm0", "psg0", "noise"]}) == {
        "name": EAR.PLATFORMS[0][1], "stereo": True}
    nes = EAR.platform_of({"columns": ["nes0", "nes1", "nes2"]})
    assert nes["stereo"] is False and "NES" in nes["name"]
    assert EAR.platform_of({"columns": ["opl0"]})["stereo"] is False


def test_a_learning_record_keeps_what_the_model_heard():
    from agentic import memory as M
    heard = {"available": True, "model": "m", "basis": "heard by model",
             "choice": 2, "labels": ["as it is", "a", "b"],
             "confidence": 0.8, "statement": "b is clearer",
             "clip": {"channels": 2}}
    a = M._auditory({"outcome": "committed", "listening_verdict": heard,
                     "judged_by": "heard by model"})
    assert a["judgement"]["choice"] == 2 and a["judgement"]["model"] == "m"
    assert a["agrees_with_measurement"] is True
    assert "nothing listened" not in a["uncertainty"]
    none = M._auditory({"outcome": "committed", "listening_verdict":
                        {"available": False}})
    assert none["judgement"] is None and "nothing listened" in \
        none["uncertainty"]


def test_a_heard_gap_is_acted_on_only_when_the_ear_chooses_a_change():
    from agentic import fixtures, guided as G, render as R
    obs = {"basis": "heard by model", "about_the_piece": True,
           "category": "balance", "direction": "less", "voices": ["pad"],
           "statement": "the pad is too loud, softer please",
           "confidence": 0.7}
    for uncertain, outcome in ((True, "rolled_back"), (False, "committed")):
        p = _project(fixtures.masked_lead())
        cache = R.Cache(p.path("renders"))
        adapter = _ChoosingEar(uncertain=uncertain, pick=1)
        rv = G.revise_heard(p, "all", [obs], ear=_choosing(adapter, cache),
                            cache=cache)
        w = rv["worked_on"][0]
        assert w["outcome"] == outcome, w
        assert w["judged_by"] == "heard by model"
        assert adapter.channels and set(adapter.channels[3:]) == {2}
        if outcome == "committed":
            assert w["chosen"] == "level_pad_-3" and p.head == 1
            d = p.state["decisions"][-1]
            assert d["raised_by"] == "heard by model"
            assert d["listening_verdict"]["choice"] == 1
            # a balance change is heard at its rendered level
            assert adapter.choices[-1]["matched"] is False
            assert d["listening_verdict"]["clip"]["matched"] is False
        else:
            assert p.head == 0 and "acted on only when" in w["why"]
        shutil.rmtree(os.path.dirname(p.root))


def test_a_heard_instrument_problem_becomes_a_program_the_ear_picks():
    from agentic import fixtures, guided as G, render as R
    with _Banks():
        p = _project(fixtures.masked_lead())
        cache = R.Cache(p.path("renders"))
        adapter = _ChoosingEar()
        obs = {"basis": "heard by model", "about_the_piece": True,
               "category": "body_brightness", "direction": "less",
               "voices": ["lead"], "statement": "the lead is harsh: "
               "less bright in its held notes", "confidence": 0.7}
        rv = G.revise_heard(p, "all", [obs], ear=_choosing(adapter, cache),
                            cache=cache, instrument_budget=2)
        w = rv["worked_on"][0]
        assert w["dimension"] == "instrument" and w["goal"], w
        assert w["outcome"] == "committed", w["why"]
        assert w["chosen"].startswith("br"), w["chosen"]
        assert w["listening_verdict"]["choice"] >= 1
        assert adapter.choices and set(adapter.channels[3:]) == {2}
        assert p.state["instruments"]["lead"]["patch"] != "square_lead"
        shutil.rmtree(os.path.dirname(p.root))


def test_a_guided_round_works_first_on_what_the_ear_heard():
    from agentic import guided as G, openmpt as O, references as REF
    if not O.available():
        support.skip("libopenmpt is not installed")
    with _Banks():
        src = _composed()
        root = _tmp()
        path = _module_of(src, root)
        p = _project(src.state)
        REF.add(p, path, tags=["rhythm"], seconds=20, check=False)
        heard = {"fragment": 1, "t0": 0.0, "t1": 2.0, "feature": "space",
                 "category": "space", "direction": "more", "voices": [],
                 "statement": "the piece is narrow; wider", "confidence": 0.7}
        adapter = _ChoosingEar(observation=heard, pick=1)
        rep = G.improve_toward(p, "A", ["ref1"], folder=os.path.join(root,
                                                                     "g"),
                               max_gaps=1, ear=_choosing(adapter))
        w = rep["worked_on"][0]
        # what the ear heard is worked on first, through the same changes
        # and guards as a measured gap; here every candidate broke one, so
        # nothing reached the ear and the piece stayed
        assert w["basis"] == "heard by model" and w["dimension"] == "space"
        assert [h["id"] for h in w["hypotheses"]] == [
            "spread", "echo", "spread+echo"]
        assert all(h["moved"] > 0 for h in w["hypotheses"])
        if w["outcome"] == "committed":
            assert w["judged_by"] == "heard by model"
        else:
            assert w["judged_by"].startswith("guards"), w["judged_by"]
            assert p.head == 0
        assert rep["listening"]["available"] is True
        shutil.rmtree(root)
