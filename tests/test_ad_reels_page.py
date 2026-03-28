"""GET /ad-reels must return the Reels AD HTML page, not JSON (e.g. 404 ``{\"detail\":...}``)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi.testclient import TestClient

import velo_web


class TestAdReelsPage(unittest.TestCase):
    def test_ad_reels_returns_html_not_json(self) -> None:
        with TestClient(velo_web.app) as client:
            response = client.get("/ad-reels")

        self.assertEqual(response.status_code, 200)

        content_type = response.headers.get("content-type", "")
        self.assertIn(
            "text/html",
            content_type,
            msg=f"expected HTML content-type, got {content_type!r}",
        )
        primary = content_type.split(";")[0].strip().lower()
        self.assertNotEqual(
            primary,
            "application/json",
            msg="endpoint must not respond as application/json",
        )

        body = response.text
        self.assertIn("<html", body.lower())
        self.assertIn("reels ad", body.lower())
        self.assertFalse(
            body.lstrip().startswith("{"),
            msg="body must not be a JSON object (e.g. FastAPI error detail)",
        )


if __name__ == "__main__":
    unittest.main()
