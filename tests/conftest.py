"""Make `pytest tests/` work the same way `python3 tests/run_tests.py` does.

The engine lives in python/ and is imported by plain module name (there is
no package and no install step — that is deliberate, it is what lets the
bridge archive run straight out of a zip). pytest does not know that, so
this puts both directories on the path before collection.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))

for path in (os.path.join(ROOT, "python"), HERE):
    if path not in sys.path:
        sys.path.insert(0, path)
