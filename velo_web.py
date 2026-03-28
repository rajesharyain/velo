"""
Uvicorn entry point: put the repository root first on ``sys.path`` so
``import travel_instagram`` always resolves to this checkout (never a stale
site-packages copy), then expose the FastAPI app (including ``/ad-reels``).

Run from repo root::

    python -m uvicorn velo_web:app --reload --host 127.0.0.1 --port 8000

Or use ``.\\run_web.ps1``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
_root = str(_REPO_ROOT)
if sys.path[0] != _root:
    try:
        sys.path.remove(_root)
    except ValueError:
        pass
    sys.path.insert(0, _root)

from travel_instagram.web_app import app  # noqa: E402

__all__ = ["app"]
