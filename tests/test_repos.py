"""リポジトリの取り込み（company/repos.py）。業種の当て方、木の形、org との合体。GitHub は偽の一覧で。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from company import org, repos  # noqa: E402

FAKE = [
    {"name": "TrendGame", "full_name": "o/TrendGame", "description": "ゲーム", "topics": [], "pushed_at": "2026-11-01T00:00:00Z", "private": True},
    {"name": "FX", "full_name": "o/FX", "description": "為替の自動売買", "topics": [], "pushed_at": "2025-01-01T00:00:00Z", "private": True},
    {"name": "NetVision", "full_name": "o/NetVision", "description": "台帳", "topics": ["network"], "pushed_at": "2026-10-01T00:00:00Z", "private": False},
    {"name": "Old", "full_name": "o/Old", "description": "", "topics": [], "pushed_at": "2024-01-01T00:00:00Z", "archived": True},
    {"name": "Forked", "full_name": "o/Forked", "description": "", "topics": [], "pushed_at": "2026-11-01T00:00:00Z", "fork": True},
]


class ReposTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(repos.classify("TrendGame", "ゲーム"), "ゲーム")
        self.assertEqual(repos.classify("FX", "為替"), "金融")
        self.assertEqual(repos.classify("daily-news"), "メディア")
        self.assertEqual(repos.classify("zzz"), "その他")

    def test_build_tree_groups_by_industry_and_skips_manual_archived_fork(self):
        nodes = repos.build_tree(FAKE, manual_names={"NetVision"})
        names = {n["name"]: [c["name"] for c in n["children"]] for n in nodes}
        self.assertEqual(names, {"ゲームグループ": ["TrendGame"], "金融グループ": ["FX"]})
        self.assertTrue(all(n["generated"] for n in nodes))

    def test_generated_tree_merges_into_org_and_validates(self):
        with tempfile.TemporaryDirectory() as d:
            gen = Path(d) / "org.generated.yaml"
            repos.write_generated(repos.build_tree(FAKE, {"NetVision"}), gen)
            with mock.patch.object(repos, "GENERATED_PATH", gen):
                tree = org.load()
            kinds = [c["kind"] for c in tree["children"]]
            self.assertIn("division", kinds)
            self.assertEqual(org.validate(tree, {p["id"] for p in __import__("company.partners", fromlist=["x"]).load()},
                                          {"ceo", "cfo", "ops", "cto", "cmo", "builder"}), [])
            self.assertIn("NetVision", org.manual_names())

    def test_stale(self):
        self.assertEqual(repos.stale(FAKE, days=90), ["FX"])

    def test_sync_uses_fake_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(repos, "fetch", return_value=FAKE), \
                 mock.patch.object(repos, "CACHE_PATH", Path(d) / "repos.json"), \
                 mock.patch.object(repos, "GENERATED_PATH", Path(d) / "org.generated.yaml"):
                r = repos.sync("o", "", {"NetVision"})
                self.assertEqual((r["repos"], r["subsidiaries"]), (5, 2))
                self.assertTrue((Path(d) / "repos.json").exists())


if __name__ == "__main__":
    unittest.main()
