from __future__ import annotations

import sys
from pathlib import Path


def bootstrap_candidate_core() -> Path:
    root = Path(__file__).resolve().parent.parent / "candidate_core"
    text = str(root)
    if text not in sys.path:
        sys.path.insert(0, text)
    return root
