"""The manual is tested: its tables are the code's, its examples compile."""

import importlib.util
import os
import re

import support
from schism import notation

DOCS = os.path.join(support.ROOT, "docs")
FENCE = re.compile(r"^```(\S*)\n(.*?)^```$", re.S | re.M)


def _make_docs():
    spec = importlib.util.spec_from_file_location(
        "make_docs", os.path.join(support.ROOT, "tools", "make_docs.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fences(path):
    with open(path, encoding="utf-8") as handle:
        return FENCE.findall(handle.read())


def test_the_generated_tables_are_what_the_code_says():
    make_docs = _make_docs()
    for path in make_docs.DOCS:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            written = handle.read()
        assert make_docs.render(written) == written, \
            f"{os.path.basename(path)} is stale: python3 tools/make_docs.py"


def test_every_wave_and_every_setting_is_in_the_manual():
    from schism import recipes
    with open(os.path.join(DOCS, "NOTATION.md"), encoding="utf-8") as handle:
        text = handle.read()
    for kind in recipes.WAVES:
        assert f"| `{kind}` |" in text, kind
    for key in recipes.SETTINGS:
        assert f"`{key}=`" in text, key


def test_every_example_in_the_manual_compiles_as_it_says():
    seen = {"sch": 0, "sch-warn": 0, "sch-bad": 0}
    for name in sorted(os.listdir(DOCS)):
        if not name.endswith(".md"):
            continue
        for kind, body in _fences(os.path.join(DOCS, name)):
            if kind not in seen:
                continue
            seen[kind] += 1
            if kind == "sch-bad":
                try:
                    notation.compile_text(body)
                except notation.NotationError:
                    continue
                raise AssertionError(f"{name}: a sch-bad example compiled")
            _, report = notation.compile_text(body)
            if kind == "sch":
                assert report.warnings == (), (name, report.warnings)
            else:
                assert report.warnings, (name, "a sch-warn example is clean")
    assert seen["sch"] >= 2 and seen["sch-warn"] >= 1 and seen["sch-bad"] >= 1


def test_the_error_printed_beside_the_bad_example_is_the_real_one():
    make_docs = _make_docs()
    fences = _fences(os.path.join(DOCS, "NOTATION.md"))
    for index, (kind, body) in enumerate(fences):
        if kind == "sch-bad":
            try:
                notation.compile_text(body)
            except notation.NotationError as error:
                assert fences[index + 1][1].strip() == str(error).strip()
                return
    raise AssertionError("no sch-bad example found")
