"""HTTP API の検証。偽のローカルLLMに向けて、UIが使う経路を一通り通す。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from company import runner, server  # noqa: E402
from company import tools as toolkit  # noqa: E402
from tests.test_loop import FakeLLM  # noqa: E402


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root, self._settings = toolkit.ROOT, runner.SETTINGS_PATH
        toolkit.ROOT = self.tmp
        runner.SETTINGS_PATH = self.tmp / "settings.json"
        self.client = TestClient(server.app)

    def tearDown(self):
        toolkit.ROOT, runner.SETTINGS_PATH = self._root, self._settings

    def test_state_lists_roles_and_cadences(self):
        s = self.client.get("/api/state").json()
        self.assertIn("build", s["cadences"])
        self.assertIn("builder", [r["role"] for r in s["roles"]])
        self.assertEqual(s["settings"]["provider"], "builtin")

    def test_settings_round_trip(self):
        r = self.client.post("/api/settings", json={"model": "fake-7b", "temperature": 0.7}).json()
        self.assertEqual(r["model"], "fake-7b")
        self.assertEqual(self.client.get("/api/state").json()["settings"]["temperature"], 0.7)

    def test_models_endpoint_reports_unreachable_server(self):
        r = self.client.get("/api/models", params={"provider": "openai_compatible", "base_url": "http://127.0.0.1:9/v1"}).json()
        self.assertEqual(r["models"], [])
        self.assertIn("接続できません", r["error"])

    def test_role_run_streams_events_and_finishes(self):
        llm = FakeLLM([
            {"tool": ("write_file", {"path": "note.md", "content": "x"})},
            {"text": "追加しました"},
        ])
        try:
            self.client.post("/api/settings", json={"provider": "openai_compatible", "base_url": llm.url, "model": "fake-7b"})
            run = self.client.post("/api/runs", json={"kind": "role", "target": "builder", "instruction": "メモを書いて"}).json()
            with self.client.stream("GET", f"/api/runs/{run['id']}/events") as resp:
                body = "".join(resp.iter_text())
        finally:
            llm.close()
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        self.assertEqual(events[-1]["status"], "done")  # 最後は event: end の要約
        types = [e["type"] for e in events if "type" in e]
        self.assertEqual(types[0], "role_start")
        self.assertIn("tool_call", types)
        self.assertIn("text", types)
        self.assertEqual(self.client.get(f"/api/runs/{run['id']}").json()["status"], "done")
        self.assertTrue((self.tmp / "note.md").exists())
        # オーナーの指示がプロンプトに入っていること
        self.assertIn("メモを書いて", llm.requests[0]["messages"][-1]["content"])

    def test_unknown_role_is_rejected(self):
        r = self.client.post("/api/runs", json={"kind": "role", "target": "nobody"})
        self.assertEqual(r.status_code, 400)

    def test_file_api_stays_inside_repo(self):
        self.assertEqual(self.client.get("/api/files/../../etc/passwd").status_code, 404)
        self.assertEqual(self.client.get("/api/files/company.yaml").status_code, 200)

    def test_ui_is_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Company", r.text)


if __name__ == "__main__":
    unittest.main()
