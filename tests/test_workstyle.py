"""役職の働き方（company/workstyle.py）。Ciel のスキルと CLAUDE.md の決めたことが毎回の指示に入ることを見る。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from company import runner, workstyle  # noqa: E402
from company.config import ROOT  # noqa: E402


class WorkstyleTest(unittest.TestCase):
    def test_build_includes_core_skills_decisions_and_lessons(self):
        text = workstyle.build()
        for name in ("work-style", "finish-first", "verify-before-done", "follow-instructions"):
            self.assertIn(f"## {name}", text)
        self.assertIn("## オーナーが決めたこと", text)
        self.assertIn("ユーザーがすべての一番上", text)
        self.assertIn("## 失敗の記録（次から）", text)
        self.assertLess(len(text), 13_000, "長すぎると役職の文脈を食う")

    def test_small_context_gets_only_the_top(self):
        text = workstyle.build(max_chars=workstyle.budget_for(4096))
        self.assertLessEqual(len(text), 2_100)
        self.assertIn("## オーナーが決めたこと", text)
        self.assertNotIn("## tidy", text)

    def test_digest_is_clipped_at_line_boundary(self):
        d = workstyle.skill_digest("work-style")
        self.assertIsNotNone(d)
        self.assertLessEqual(len(d), workstyle.PER_SKILL_CHARS + 20)
        self.assertNotIn("\n---", d[:10])

    def test_without_skills_falls_back_to_short_rules(self):
        with tempfile.TemporaryDirectory() as d:
            text = workstyle.build(Path(d) / "skills", Path(d))
        self.assertIn("1 つずつやる", text)
        self.assertNotIn("## work-style", text)

    def test_role_system_prompt_starts_with_workstyle(self):
        src = (ROOT / "company" / "runner.py").read_text(encoding="utf-8")
        self.assertIn("workstyle.build(", src)
        self.assertIn("agent.instructions", src)
        self.assertTrue(hasattr(runner, "workstyle"))


if __name__ == "__main__":
    unittest.main()
