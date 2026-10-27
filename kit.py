#!/usr/bin/env python3
"""Run the account research kit: `python3 kit.py <step>`.

Steps: check, intake, filter, jobs, batches, score. See README.md.
No dependencies beyond the Python standard library (3.9+).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from account_kit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
