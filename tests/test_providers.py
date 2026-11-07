"""プロバイダの一覧と組み立て、役職ごとの割り当ての検証。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from company import config as config_module  # noqa: E402
from company import providers, runner, server  # noqa: E402
from company import tools as toolkit  # noqa: E402
from tests.test_loop import FakeLLM  # noqa: E402


class PresetTest(unittest.TestCase):
    def test_every_preset_builds_an_openai_compatible_provider(self):
        for preset in providers.PRESETS:
            with mock.patch.dict(os.environ, {preset["key_env"]: "k"} if preset["key_env"] else {}):
                p = providers.make_provider({"provider": preset["id"], "model": "m"})
            self.assertIsInstance(p, providers.OpenAICompatibleProvider, preset["id"])
            self.assertEqual(p.base_url, preset["base_url"].rstrip("/"), preset["id"])

    def test_cloud_preset_without_key_names_the_env_var(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(providers.ProviderError) as ctx:
                providers.make_provider({"provider": "openai", "model": "gpt"})
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_panel_key_beats_env(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "env"}):
            p = providers.make_provider({"provider": "groq", "model": "m", "api_key": "panel"})
        self.assertEqual(p.api_key, "panel")

    def test_local_presets_need_no_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            p = providers.make_provider({"provider": "ollama", "model": "m"})
        self.assertEqual(p.api_key, "")

    def test_catalog_lists_native_and_presets_without_values(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}):
            cat = providers.provider_catalog()
        ids = [c["id"] for c in cat]
        self.assertEqual(ids[:5], ["builtin", "aas", "gemini", "anthropic", "openai_compatible"])
        self.assertIn("openai", ids)
        openai = next(c for c in cat if c["id"] == "openai")
        self.assertTrue(openai["needs_key"] and openai["key_in_env"])
        self.assertNotIn("secret", str(cat))


class RoleSettingsTest(unittest.TestCase):
    def test_override_only_non_empty_values(self):
        base = {"provider": "builtin", "model": "a", "temperature": 0.2, "roles": {"ceo": {"provider": "openai", "model": "", "api_key": ""}}}
        s = runner.settings_for_role(base, "ceo")
        self.assertEqual(s["provider"], "openai")
        self.assertEqual(s["model"], "a")
        self.assertEqual(runner.settings_for_role(base, "ops")["provider"], "builtin")

    def test_save_rejects_non_dict_roles(self):
        tmp = Path(tempfile.mkdtemp())
        orig = runner.SETTINGS_PATH
        runner.SETTINGS_PATH = tmp / "s.json"
        try:
            self.assertEqual(runner.save_settings({"roles": "x"})["roles"], {})
            self.assertEqual(runner.save_settings({"roles": {"ceo": {"model": "m"}}})["roles"], {"ceo": {"model": "m"}})
        finally:
            runner.SETTINGS_PATH = orig


class RoleAssignmentThroughRunTest(unittest.TestCase):
    """build サイクルで、ceo だけ別の AI（別の偽サーバー）に向く。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root, self._settings, self._runs, self._dec = toolkit.ROOT, runner.SETTINGS_PATH, config_module.RUNS_PATH, runner.DECISIONS_DIR
        toolkit.ROOT = self.tmp
        runner.SETTINGS_PATH = self.tmp / "settings.json"
        config_module.RUNS_PATH = self.tmp / "runs.csv"
        runner.DECISIONS_DIR = self.tmp / "decisions"   # サイクルの記録を本物の data/ に書かない
        self.client = TestClient(server.app)

    def tearDown(self):
        toolkit.ROOT, runner.SETTINGS_PATH, config_module.RUNS_PATH, runner.DECISIONS_DIR = self._root, self._settings, self._runs, self._dec

    def test_ceo_goes_to_its_own_provider(self):
        default_llm = FakeLLM([{"text": "builder 側"}])
        ceo_llm = FakeLLM([{"text": "ceo 側"}])
        try:
            self.client.post("/api/settings", json={
                "provider": "openai_compatible", "base_url": default_llm.url, "model": "fake-default",
                "roles": {"ceo": {"provider": "openai_compatible", "base_url": ceo_llm.url, "model": "fake-ceo"}},
            })
            started = self.client.post("/api/runs", json={"kind": "cycle", "target": "build"}).json()
            run = runner.REGISTRY.get(started["id"])
            for _ in range(200):
                if run.status != "running":
                    break
                run.wait_for(len(run.events), 0.1)
        finally:
            default_llm.close(); ceo_llm.close()
        self.assertEqual(run.status, "done")
        self.assertEqual([r["model"] for r in ceo_llm.requests], ["fake-ceo"])
        self.assertEqual([r["model"] for r in default_llm.requests], ["fake-default"])
        starts = [e for e in run.events if e["type"] == "role_start"]
        self.assertEqual([(e["role"], e["model"]) for e in starts], [("ceo", "fake-ceo"), ("builder", "fake-default")])
        rows = (self.tmp / "runs.csv").read_text(encoding="utf-8").splitlines()
        self.assertIn(",ceo,openai_compatible,fake-ceo,", rows[1])
        self.assertEqual(len(list((self.tmp / "decisions").glob("*.md"))), 1)

    def test_providers_endpoint(self):
        ids = [p["id"] for p in self.client.get("/api/providers").json()["providers"]]
        self.assertIn("openrouter", ids)


if __name__ == "__main__":
    unittest.main()


class ErrorWordingTest(unittest.TestCase):
    """失敗を人の言葉で言う。生の番号や errno だけでは次の一手が分からない。"""

    def test_http_status_becomes_kind(self):
        self.assertEqual(providers.classify_http_error(401, ""), "auth")
        self.assertEqual(providers.classify_http_error(429, '{"error":{"code":"rate_limit_exceeded"}}'), "rate_limited")
        self.assertEqual(providers.classify_http_error(429, '{"error":{"code":"insufficient_quota"}}'), "quota_exhausted")
        self.assertEqual(providers.classify_http_error(404, 'model "x" not found'), "model_missing")
        self.assertEqual(providers.classify_http_error(404, "no route"), "invalid_request")
        self.assertEqual(providers.classify_http_error(400, "context_length_exceeded"), "context_too_long")
        self.assertEqual(providers.classify_http_error(503, ""), "unavailable")

    def test_http_error_message_is_japanese_and_keeps_detail(self):
        err = providers.http_error("http://x/v1/chat/completions", 401, "Unauthorized")
        self.assertEqual(err.kind, "auth")
        self.assertIn("API キー", str(err))
        self.assertIn("HTTP 401", str(err))

    def test_network_reason_is_explained(self):
        self.assertIn("断られました", providers.explain_network_error("[Errno 111] Connection refused"))
        self.assertIn("名前を引けません", providers.explain_network_error("[Errno -2] Name or service not known"))

    def test_dead_server_fails_fast_with_wording(self):
        import socket
        import time
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        p = providers.OpenAICompatibleProvider(f"http://127.0.0.1:{port}/v1", model="m", timeout=600, connect_timeout=2)
        started = time.time()
        with self.assertRaises(providers.ProviderError) as ctx:
            p.list_models()
        self.assertLess(time.time() - started, 5)
        self.assertEqual(ctx.exception.kind, "unavailable")
        self.assertIn("断られました", str(ctx.exception))


class AnswerCheckTest(unittest.TestCase):
    """「答えるか確かめる」は 1 問送って、答えと秒数を返す。"""

    def setUp(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                Handler.seen = body
                out = json.dumps({"choices": [{"message": {"role": "assistant", "content": "2"}}],
                                  "usage": {"prompt_tokens": 5, "completion_tokens": 1}}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

        self.handler = Handler
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.tmp = Path(tempfile.mkdtemp())
        self._settings = runner.SETTINGS_PATH
        runner.SETTINGS_PATH = self.tmp / "settings.json"

    def tearDown(self):
        self.server.shutdown()
        runner.SETTINGS_PATH = self._settings

    def test_answer_check_returns_answer_and_seconds(self):
        r = providers.answer_check({"provider": "openai_compatible", "base_url": self.url, "model": "m"})
        self.assertEqual(r["answer"], "2")
        self.assertEqual(r["question"], "1+1=")
        self.assertIsInstance(r["seconds"], float)
        self.assertNotIn("tools", self.handler.seen)

    def test_check_endpoint_reports_error_in_words(self):
        client = TestClient(server.app)
        ok = client.post("/api/check", json={"provider": "openai_compatible", "base_url": self.url, "model": "m"}).json()
        self.assertEqual(ok["answer"], "2")
        with mock.patch.dict(os.environ, {}, clear=True):
            bad = client.post("/api/check", json={"provider": "openai", "model": "gpt"}).json()
        self.assertIn("OPENAI_API_KEY", bad["error"])
