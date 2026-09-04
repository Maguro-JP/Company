"""APIを呼ばずにサイクル全体を検証する。

anthropic クライアントを差し替え、実行順・上流報告の受け渡し・記録の書き出しを確認する。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from company import board, context  # noqa: E402
from company.config import Config  # noqa: E402

CONFIG = Config(
    {
        "company": {"name": "テスト社", "mission": "検証", "stage": "idea"},
        "blocked_on_owner": ["法的拘束力のある契約への署名"],
        "autonomy": {"write_code": True, "publish_externally": True, "spend_money": False},
        "kpi": {"north_star": "週次課金ユーザー", "guardrails": ["現金がゼロを下回らない"]},
        "cadence": {"weekly": ["ops", "ceo", "cfo"]},
        "model": {"default": "model-default", "deep": "model-deep"},
    }
)


class StubClient:
    """呼び出しの記録だけを行う偽クライアント。"""

    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, *, model, max_tokens, system, messages):
        self.calls.append({"model": model, "system": system, "prompt": messages[0]["content"]})
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=f"{model} からの報告")]
        )


class CycleTest(unittest.TestCase):
    def setUp(self):
        self.client = StubClient()
        self._orig = board._client
        board._client = lambda: self.client

    def tearDown(self):
        board._client = self._orig

    def test_roles_run_in_fixed_order(self):
        """company.yaml の記載順ではなく ROLE_ORDER に従う。"""
        reports = board.run_cycle(CONFIG, "weekly")
        self.assertEqual([r.agent.role for r in reports], ["ceo", "cfo", "ops"])

    def test_model_tier_is_resolved_per_role(self):
        board.run_cycle(CONFIG, "weekly")
        # ceo は deep、cfo と ops は default
        self.assertEqual([c["model"] for c in self.client.calls],
                         ["model-deep", "model-default", "model-default"])

    def test_downstream_receives_upstream_reports(self):
        """先行役職の報告が、後続のプロンプトに含まれること。"""
        board.run_cycle(CONFIG, "weekly")
        first, second = self.client.calls[0]["prompt"], self.client.calls[1]["prompt"]
        self.assertNotIn("先に報告された内容", first)
        self.assertIn("先に報告された内容", second)
        self.assertIn("model-deep からの報告", second)

    def test_autonomy_and_blockers_reach_the_prompt(self):
        """許可されている実行範囲と、手段が無い作業の両方が届くこと。"""
        board.run_cycle(CONFIG, "weekly")
        prompt = self.client.calls[0]["prompt"]
        self.assertIn("write_code: True", prompt)
        self.assertIn("提案で止めないこと", prompt)
        self.assertIn("法的拘束力のある契約への署名", prompt)

    def test_record_is_written_with_approval_section(self):
        reports = board.run_cycle(CONFIG, "weekly")
        with tempfile.TemporaryDirectory() as tmp:
            board.DECISIONS_DIR = Path(tmp)
            path = board.write_record(CONFIG, "weekly", reports)
            body = path.read_text(encoding="utf-8")
        self.assertIn("weekly", path.name)
        self.assertIn("最高経営責任者", body)
        self.assertIn("オーナー承認欄", body)

    def test_second_record_same_day_does_not_overwrite(self):
        reports = board.run_cycle(CONFIG, "weekly")
        with tempfile.TemporaryDirectory() as tmp:
            board.DECISIONS_DIR = Path(tmp)
            first = board.write_record(CONFIG, "weekly", reports)
            second = board.write_record(CONFIG, "weekly", reports)
        self.assertNotEqual(first.name, second.name)

    def test_missing_metrics_are_reported_as_absent(self):
        """metrics.csv に実データが無いとき、数値を作らせない指示が入ること。"""
        block = context.build(CONFIG, "weekly")
        self.assertIn("データなし", block)
        self.assertIn("創作しないこと", block)

    def test_unknown_cadence_fails_loudly(self):
        with self.assertRaises(SystemExit):
            board.run_cycle(CONFIG, "daily")


if __name__ == "__main__":
    unittest.main()
