"""会社の形（company/org.py、org.yaml）。5 段の木、席と役職の検証、API。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from company import org, partners, runner, server  # noqa: E402


class OrgTest(unittest.TestCase):
    def test_default_tree_is_valid_and_five_levels(self):
        tree = org.load()
        known = {p["id"] for p in partners.load()}
        roles = {r["role"] for r in runner.list_roles()}
        self.assertEqual(org.validate(tree, known, roles), [])
        kinds = {n.get("kind") for _, n in org.walk(tree)}
        self.assertEqual(kinds, set(org.KINDS))
        self.assertEqual(tree["kind"], "holding")
        self.assertEqual(tree["name"], "Company")
        self.assertEqual({c["kind"] for c in tree["children"]}, {"company", "division"})
        self.assertIn("法務部門", [c["name"] for c in tree["children"]])
        self.assertIn("ceo", tree["roles"])
        self.assertGreaterEqual(len(org.sections(tree)), 28)

    def test_every_seat_sits_in_exactly_one_section(self):
        tree = org.load()
        idx = org.seat_index(tree)
        for p in partners.load():
            self.assertIn(p["id"], idx, f"席 {p['id']} がどの課にも無い")

    def test_validate_reports_unknown_and_duplicate(self):
        tree = {"name": "x", "kind": "holding", "children": [
            {"name": "c", "kind": "company", "children": [
                {"name": "s1", "kind": "section", "seats": ["aas", "nope"], "roles": ["ceo", "king"]},
                {"name": "s2", "kind": "section", "seats": ["aas"]},
                {"name": "bad", "kind": "team"},
            ]}]}
        errs = org.validate(tree, {"aas"}, {"ceo"})
        joined = "\n".join(errs)
        self.assertIn("nope", joined)
        self.assertIn("king", joined)
        self.assertIn("にもあります", joined)
        self.assertIn("team", joined)

    def test_annotate_counts_alive_and_planned(self):
        tree = {"name": "h", "kind": "holding", "children": [
            {"name": "s", "kind": "section", "seats": ["a", "b", "c"]}]}
        probes = {"a": {"id": "a", "name": "A", "alive": True}, "b": {"id": "b", "name": "B", "alive": False},
                  "c": {"id": "c", "name": "C", "alive": False, "planned": True}}
        out = org.annotate(tree, probes)
        self.assertEqual((out["total"], out["alive"], out["planned"]), (3, 1, 1))
        self.assertEqual(out["label"], "親会社")
        self.assertEqual(out["children"][0]["label"], "課")

    def test_api_get_and_rejects_bad_tree(self):
        client = TestClient(server.app)
        r = client.get("/api/org").json()
        self.assertEqual(r["tree"]["kind"], "holding")
        self.assertEqual(r["errors"], [])
        bad = client.post("/api/org", json={"tree": {"name": "x", "kind": "holding", "children": [
            {"name": "s", "kind": "section", "seats": ["nope"]}]}})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("nope", bad.json()["detail"])


if __name__ == "__main__":
    unittest.main()
