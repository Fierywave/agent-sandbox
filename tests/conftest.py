"""Adds the repo root (for `contracts`) and each track's directory to
sys.path, since track-a-guardrail/ and track-b-agent/ have hyphens in
their names and can't be imported as dotted Python packages. Run pytest
from the repo root: `python -m pytest tests/ -q`.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
for p in [REPO_ROOT, REPO_ROOT / "track-a-guardrail", REPO_ROOT / "track-b-agent"]:
    p_str = str(p.resolve())
    if p_str not in sys.path:
        sys.path.insert(0, p_str)