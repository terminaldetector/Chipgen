"""Choosing instruments by measurement — and proving the choice moves."""


def test_genre_actually_changes_the_instrument():
    # The whole claim of selection.py is that a genre word reaches the
    # chip. If ambient and hardcore pick the same lead, it does not, and
    # the genre table is decoration.
    import selection

    ambient = selection.cast("lead", "ambient", limit=1)[0]["name"]
    hardcore = selection.cast("lead", "hardcore", limit=1)[0]["name"]
    assert ambient != hardcore, \
        f"ambient and hardcore both chose {ambient!r} as the lead"


def test_a_role_picks_something_that_measures_like_that_role():
    # Not "is named like" — measured. The bass must be darker than the
    # lead and the pad must ring longer than the pluck, whatever they are
    # called.
    import selection

    characteristics = selection.audition.characteristics()
    bass = selection.cast("bass", limit=1)[0]["name"]
    lead = selection.cast("lead", limit=1)[0]["name"]
    assert characteristics[bass]["brightness"] < characteristics[lead]["brightness"], \
        f"bass {bass} is not darker than lead {lead}"

    pad = selection.cast("pad", limit=1)[0]["name"]
    pluck = selection.cast("pluck", limit=1)[0]["name"]
    assert characteristics[pad]["release"] > characteristics[pluck]["release"], \
        f"pad {pad} does not ring longer than pluck {pluck}"


def test_bass_is_only_cast_from_patches_that_sound_at_c2():
    # Ranks are relative, so without a register gate the best-ranked patch
    # in a bank of leads is still a lead when you ask for a bass.
    import selection

    characteristics = selection.audition.characteristics()
    for row in selection.cast("bass", limit=5):
        audible = characteristics[row["name"]]["audible_notes"]
        assert "C-2" in audible, \
            f"{row['name']} was cast as bass but is not audible at C-2"


def test_a_palette_does_not_seat_one_patch_twice():
    import selection

    chosen = selection.palette("melodic")
    names = [row["name"] for row in chosen.values()]
    assert len(names) == len(set(names)), f"duplicate in palette: {names}"
    assert set(chosen) == set(selection.DEFAULT_PARTS)


def test_every_choice_carries_the_measurements_that_made_it():
    # A pick without a reason is a pick by name again. This is what lets a
    # model disagree with the table rather than take it on faith.
    import selection

    for genre in (None, "hardcore", "jrpg"):
        for row in selection.cast("lead", genre, limit=2):
            assert row["why"] and row["why"] != "closest available fit", \
                f"{row['name']} for {genre} came with no measured reason"


def test_ranks_do_not_spread_tied_patches_by_list_order():
    # Twelve of nineteen patches use attack rate 31 and genuinely tie at
    # 0 ms. Ranking them 0.0 to 1.0 by list position would invent a
    # difference that is not in the audio.
    import selection

    ranks = selection._ranks({
        "a": {"usable": True, "attack": 0.0, "brightness": 1.0},
        "b": {"usable": True, "attack": 0.0, "brightness": 2.0},
        "c": {"usable": True, "attack": 0.0, "brightness": 3.0},
        "d": {"usable": True, "attack": 0.5, "brightness": 4.0},
    })
    tied = {ranks[name]["attack"] for name in ("a", "b", "c")}
    assert len(tied) == 1, f"tied attacks got different ranks: {tied}"
    assert ranks["d"]["attack"] > next(iter(tied))


def test_unknown_role_or_genre_is_an_error_not_a_silent_default():
    import selection

    for bad in ("definitely_not_a_role",):
        try:
            selection.targets(bad)
        except KeyError as error:
            assert "have:" in str(error)
        else:
            raise AssertionError("unknown role was accepted")
    try:
        selection.targets("lead", "definitely_not_a_genre")
    except KeyError as error:
        assert "have:" in str(error)
    else:
        raise AssertionError("unknown genre was accepted")


def test_a_patch_is_keyed_by_what_it_is_not_by_its_name():
    """Two banks can both contain a `bass`, and a patch can be edited.

    The measurement cache was keyed by name alone, so either of those
    silently handed one patch another's numbers. The fingerprint covers
    algorithm, feedback, trim and every operator field — everything that
    changes how a patch sounds.
    """
    import audition
    import instruments

    original = instruments.BANK["bass"]
    assert audition.fingerprint(original) == audition.fingerprint(original), \
        "the fingerprint is not stable across calls"

    louder = original.copy()
    louder.operators[0].total_level += 1
    assert audition.fingerprint(louder) != audition.fingerprint(original), \
        "an edited operator level did not change the fingerprint"

    retuned = original.copy()
    retuned.trim = (retuned.trim or 0) - 4
    assert audition.fingerprint(retuned) != audition.fingerprint(original), (
        "trim did not change the fingerprint — measurements are taken "
        "with it applied, so a recalibrated bank must re-measure")

    renamed = original.copy()
    renamed.name = "definitely_not_bass"
    assert audition.fingerprint(renamed) == audition.fingerprint(original), \
        "the name leaked into the fingerprint; the same patch is the same"


def test_measuring_an_imported_bank_leaves_the_built_in_cache_alone():
    """Importing a bank used to overwrite bank_audition.json.

    Both caches were one file keyed by name, so measuring an imported
    bank replaced the built-in bank's shipped measurements — after which
    a plain `--cast` re-measured all 19 from scratch as well.
    """
    import json
    import os

    import audition
    import instruments
    import support

    before = json.load(open(audition.CACHE_PATH, encoding="utf-8"))
    original = dict(instruments.BANK)
    with support.TempDir() as tmp:
        try:
            patch = instruments.BANK["bass"].copy()
            patch.name = "imported_thing"
            patch.operators[0].total_level = min(
                127, patch.operators[0].total_level + 3)
            path = os.path.join(tmp, "bank.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([instruments.instrument_to_dict(patch)], handle)
            instruments.load_bank(path)

            got = audition.characteristics()
            assert "imported_thing" in got, \
                "the imported patch was not measured"
            assert "bass" in got, "the built-in patches went missing"
        finally:
            instruments.BANK.clear()
            instruments.BANK.update(original)

    after = json.load(open(audition.CACHE_PATH, encoding="utf-8"))
    assert after == before, (
        "measuring an imported bank rewrote bank_audition.json — the "
        "built-in bank's shipped measurements are not the import's to "
        "touch")


def test_characteristics_only_measures_what_it_does_not_already_have():
    # The cache was all-or-nothing: one unknown name meant re-measuring
    # every patch in the bank. On a 175-patch import that is minutes,
    # every time, which is what made selection-by-measurement impractical
    # on a real corpus.
    import audition
    import instruments

    import random

    measured = []
    original = dict(instruments.BANK)
    try:
        # The patch has to be one the index has genuinely never seen, or
        # this measures nothing and passes for the wrong reason. An
        # earlier run of this very test cached its own fixture and made
        # the suite order-dependent — green on a cold cache, red on a
        # warm one. Randomising the operator levels makes the fingerprint
        # fresh every run.
        patch = instruments.BANK["organ"].copy()
        patch.name = "one_new_patch"
        for operator in patch.operators:
            operator.total_level = random.randint(1, 40)
        instruments.BANK["one_new_patch"] = patch
        assert audition.fingerprint(patch) not in audition.load_index(), \
            "the fixture is already cached; the test would prove nothing"

        audition.characteristics(
            progress=lambda i, total, name: measured.append(name))
        assert measured == ["one_new_patch"], (
            f"adding one patch re-measured {len(measured)} of "
            f"{len(instruments.BANK)}: {measured[:5]}")
    finally:
        instruments.BANK.clear()
        instruments.BANK.update(original)
