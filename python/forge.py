#!/usr/bin/env python3
"""forge.py — the instrument/driver synthesis layer's command line.

    python3 python/forge.py axes
    python3 python/forge.py compile ym2612 --archetype slap_bass
    python3 python/forge.py run scenario.json --out work/lead.bank.json
    python3 python/chipgen.py song.trk --forge-bank work/lead.bank.json

See `python3 python/forge.py --help` and docs/SYNTHESIS.md.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from synthesis.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
