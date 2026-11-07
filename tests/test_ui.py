"""画面のファイルの検証。役職とキャラクターがそろっているかを見る。"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from company.config import AGENTS_DIR, ROOT  # noqa: E402

UI = ROOT / "ui" / "index.html"


class CharacterTest(unittest.TestCase):
    def test_every_role_has_a_look(self):
        html = UI.read_text(encoding="utf-8")
        looks = re.search(r"const LOOKS = \{(.*?)\n  \};", html, re.S).group(1)
        for path in AGENTS_DIR.glob("*.md"):
            self.assertRegex(looks, rf"\n\s*{path.stem}:\s*\{{", f"{path.stem} のキャラクターが LOOKS に無い")

    def test_every_role_has_a_picture(self):
        for path in AGENTS_DIR.glob("*.md"):
            pic = ROOT / "ui" / "characters" / f"{path.stem}.png"
            self.assertTrue(pic.exists(), f"{pic} が無い")
            self.assertEqual(pic.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_icon_exists(self):
        self.assertTrue((ROOT / "ui" / "icon" / "app.ico").exists())
        self.assertTrue((ROOT / "ui" / "icon" / "app-64.png").exists())

    def test_stage_and_states_exist(self):
        html = UI.read_text(encoding="utf-8")
        self.assertIn('id="stage"', html)
        for cls in ("thinking", "talking", "working", "done", "error"):
            self.assertIn(f".chara.{cls}", html)
