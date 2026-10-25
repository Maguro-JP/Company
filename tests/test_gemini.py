"""Gemini 連携の検証。偽の Gemini API に向けて、両方向を通す。"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from company import gemini, gemini_bridge, runner, server  # noqa: E402
from company import tools as toolkit  # noqa: E402
from company.loop import run_loop  # noqa: E402
from company.providers import ProviderError, make_provider  # noqa: E402


class FakeGemini:
    """generateContent に台本通り答える。functionCall も返せる。"""

    def __init__(self, script: list[dict], bad_key: bool = False):
        self.script = list(script)
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, {"models": [
                    {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
                ]})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append(json.loads(self.rfile.read(length)))
                if bad_key or self.headers.get("x-goog-api-key") != "AIza-test":
                    self._send(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
                    return
                step = outer.script.pop(0) if outer.script else {"text": "台本切れ"}
                parts = []
                if step.get("text"):
                    parts.append({"text": step["text"]})
                if "call" in step:
                    name, args = step["call"]
                    parts.append({"functionCall": {"name": name, "args": args}})
                self._send(200, {"candidates": [{"content": {"role": "model", "parts": parts}}],
                                 "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 7}})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1beta"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GeminiProviderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root = toolkit.ROOT
        toolkit.ROOT = self.tmp
        self.events = []

    def tearDown(self):
        toolkit.ROOT = self._root

    def _provider(self, fake, key="AIza-test"):
        return gemini.GeminiProvider("gemini-2.5-flash", key, base_url=fake.url)

    def test_runs_the_company_loop_with_native_function_calling(self):
        fake = FakeGemini([
            {"call": ("write_file", {"path": "hello.txt", "content": "hi"})},
            {"text": "書きました"},
        ])
        try:
            result = run_loop(self._provider(fake), "sys", "task", toolkit.toolset(True), self.events.append, role="builder")
        finally:
            fake.close()
        self.assertEqual(result.stopped_reason, "done")
        self.assertEqual((self.tmp / "hello.txt").read_text(), "hi")
        first = fake.requests[0]
        self.assertEqual(first["system_instruction"]["parts"][0]["text"], "sys")
        names = {d["name"] for d in first["tools"][0]["functionDeclarations"]}
        self.assertIn("write_file", names)
        # 2回目のリクエストには functionResponse が入っている
        last = fake.requests[1]["contents"][-1]
        self.assertEqual(last["role"], "user")
        self.assertIn("functionResponse", last["parts"][0])
        self.assertIn("書き込みました", last["parts"][0]["functionResponse"]["response"]["result"])

    def test_schema_is_stripped_to_gemini_subset(self):
        s = gemini._schema({"type": "object", "additionalProperties": False, "properties": {"a": {"type": "string", "x-extra": 1}}, "required": ["a"]})
        self.assertNotIn("additionalProperties", s)
        self.assertNotIn("x-extra", s["properties"]["a"])
        self.assertEqual(s["required"], ["a"])

    def test_invalid_key_is_explained(self):
        fake = FakeGemini([], bad_key=True)
        try:
            with self.assertRaises(ProviderError) as ctx:
                self._provider(fake).complete("s", [{"role": "user", "parts": [{"text": "hi"}]}], [], 0.2)
        finally:
            fake.close()
        self.assertIn("aistudio.google.com/apikey", str(ctx.exception))

    def test_missing_key_fails_early(self):
        with self.assertRaises(ProviderError):
            make_provider({"provider": "gemini", "model": "gemini-2.5-flash", "api_key": ""})

    def test_list_models_keeps_only_generation_models(self):
        fake = FakeGemini([])
        try:
            self.assertEqual(self._provider(fake).list_models(), ["gemini-2.5-flash"])
        finally:
            fake.close()


class CompanyFromGeminiTest(unittest.TestCase):
    """逆方向: Gemini 側の functionCall を /api/gemini/call で受けて会社を動かす。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root, self._settings = toolkit.ROOT, runner.SETTINGS_PATH
        toolkit.ROOT = self.tmp
        runner.SETTINGS_PATH = self.tmp / "settings.json"
        self.client = TestClient(server.app)

    def tearDown(self):
        toolkit.ROOT, runner.SETTINGS_PATH = self._root, self._settings

    def test_function_declarations_are_served(self):
        decl = self.client.get("/api/gemini/functions").json()
        names = {f["name"] for f in decl["tools"][0]["functionDeclarations"]}
        self.assertEqual(names, {"list_roles", "start_cycle", "start_role", "get_run", "stop_run", "read_file"})

    def test_list_roles_and_read_file(self):
        r = self.client.post("/api/gemini/call", json={"name": "list_roles"}).json()
        self.assertIn("build", r["cadences"])
        r = self.client.post("/api/gemini/call", json={"name": "read_file", "args": {"path": "company.yaml"}}).json()
        self.assertIn("mission", r["content"])

    def test_start_role_then_get_run(self):
        from tests.test_loop import FakeLLM
        llm = FakeLLM([{"text": "確認しました"}])
        try:
            self.client.post("/api/settings", json={"provider": "openai_compatible", "base_url": llm.url, "model": "fake"})
            started = self.client.post("/api/gemini/call", json={"name": "start_role", "args": {"role": "ops", "instruction": "見て"}}).json()
            self.assertIn("id", started)
            run = runner.REGISTRY.get(started["id"])
            for _ in range(100):
                if run.status != "running":
                    break
                run.wait_for(len(run.events), 0.1)
            got = self.client.post("/api/gemini/call", json={"name": "get_run", "args": {"run_id": started["id"]}}).json()
        finally:
            llm.close()
        self.assertEqual(got["status"], "done")
        self.assertIn("確認しました", [e.get("text") for e in got["events"]])

    def test_unknown_function_and_missing_args_are_errors_not_crashes(self):
        self.assertIn("error", self.client.post("/api/gemini/call", json={"name": "nope"}).json())
        self.assertIn("error", self.client.post("/api/gemini/call", json={"name": "start_role", "args": {}}).json())
        self.assertIn("error", self.client.post("/api/gemini/call", json={"name": "get_run", "args": {"run_id": "zzz"}}).json())


class BridgeTest(unittest.TestCase):
    """ブリッジ: Gemini が functionCall を返したら会社APIへ中継し、結果を返して会話を続ける。"""

    def test_bridge_relays_function_calls_to_the_app(self):
        fake = FakeGemini([
            {"call": ("list_roles", {})},
            {"text": "役職は6つあります。"},
        ])
        relayed = []
        orig_call = gemini_bridge.call_company
        gemini_bridge.call_company = lambda url, name, args: (relayed.append((name, args)) or {"roles": ["ceo"]})
        try:
            answer = gemini_bridge.chat_turn("AIza-test", "gemini-2.5-flash",
                                             [{"role": "user", "parts": [{"text": "役職を教えて"}]}], "http://app",
                                             base_url=fake.url)
        finally:
            gemini_bridge.call_company = orig_call
            fake.close()
        self.assertEqual(relayed, [("list_roles", {})])
        self.assertEqual(answer, "役職は6つあります。")
        self.assertIn("functionResponse", fake.requests[1]["contents"][-1]["parts"][0])


if __name__ == "__main__":
    unittest.main()
