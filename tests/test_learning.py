"""学習ノート（docs/knowledge/learning/）の判定。表の各行に「何か・起こし方・口」がそろっているかを見る。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from company.config import ROOT  # noqa: E402

NOTE = ROOT / "docs" / "knowledge" / "learning" / "user-tools.md"


class UserToolsNoteTest(unittest.TestCase):
    def rows(self):
        out = []
        for line in NOTE.read_text(encoding="utf-8").splitlines():
            if not line.startswith("| ") or line.startswith("| 名前") or line.startswith("| 決まり"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 4 and not cells[0].startswith(("置き場", "配布", "起動", "作業", "公開")):
                out.append(cells)
        return out

    def test_every_tool_has_what_launch_and_entry(self):
        rows = self.rows()
        self.assertGreaterEqual(len(rows), 5)
        for cells in rows:
            name, what, launch, entry = cells[0], cells[1], cells[2], cells[3]
            self.assertTrue(what, f"{name}: 何かが無い")
            self.assertTrue(launch, f"{name}: 起こし方が無い")
            self.assertTrue(entry, f"{name}: 口が無い")

    def test_note_has_goal_judgement_and_next(self):
        text = NOTE.read_text(encoding="utf-8")
        for key in ("目標:", "判定:", "現在:", "諦める条件:", "## 出典"):
            self.assertIn(key, text)
        self.assertRegex(text, r"次: .+")
