"""Shared helpers. Tiny on purpose: a test suite with its own framework is
a second thing to debug when the first one breaks."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class Skipped(Exception):
    """Raised by skip(). The bare runner catches it; so does pytest, because
    when pytest is installed this is its own skip exception."""


try:                                    # pragma: no cover - depends on the host
    import pytest as _pytest
    Skipped = _pytest.skip.Exception
except ImportError:                     # pragma: no cover
    pass


def skip(reason: str):
    raise Skipped(reason)


def need_openmpt():
    """Skip unless libopenmpt is installed (apt install libopenmpt0)."""
    from schism import openmpt
    if not openmpt.available():
        skip("libopenmpt is not installed (apt install libopenmpt0)")
