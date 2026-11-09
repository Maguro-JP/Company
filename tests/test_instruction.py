"""オーナーの指示が役職の仕事の先頭に来て、指示があるときはそれだけをやる文になることを見る。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from company import runner  # noqa: E402


class InstructionFirstTest(unittest.TestCase):
    def test_instruction_comes_first_and_only(self):
        task = runner._task_for("builder", "# 会社の現状\n長い文脈", "README に 1 行足して")
        self.assertTrue(task.startswith("# オーナーの指示（これだけをやる）"))
        self.assertLess(task.index("README に 1 行足して"), task.index("会社の現状"))
        self.assertNotIn("価値が1つ増える状態", task)  # 既定の仕事は混ぜない
        self.assertIn("指示に無いこと", task)

    def test_brief_context_is_short_and_has_no_section_dumps(self):
        from company import context as context_builder
        from company.config import load_config
        cfg = load_config()
        b = context_builder.brief(cfg)
        self.assertLess(len(b), 600)
        for heading in ("## 直近の実測値", "## 守るべきガードレール", "## 禁止事項", "承認なしでやってよいこと"):
            self.assertNotIn(heading, b)

    def test_without_instruction_default_task_after_context(self):
        task = runner._task_for("builder", "# 会社の現状", None)
        self.assertTrue(task.startswith("# 会社の現状"))
        self.assertIn("価値が1つ増える状態", task)

    def test_system_puts_owner_first_when_instructed(self):
        sysm = runner._system_for("# あなたの役割", {"context": 4096}, True)
        self.assertTrue(sysm.startswith("オーナーの指示が最優先です"))
        self.assertLess(sysm.index("オーナーの指示が最優先"), sysm.index("# あなたの役割"))
        self.assertFalse(runner._system_for("# あなたの役割", {"context": 4096}, False).startswith("オーナーの指示"))


if __name__ == "__main__":
    unittest.main()
