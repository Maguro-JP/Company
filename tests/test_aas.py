"""AdaptiveAIStudio 連携の検証。

- AAS → 会社: AAS の MCP クライアント（src/mcp/client.ts）が出す通りの HTTP を /mcp に送る
- 会社 → AAS: AAS の /api/send と /api/events を模した偽サーバーで、返事が1往復に畳まれること
"""

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

from company import aas, runner, server  # noqa: E402
from company import config as config_module  # noqa: E402
from company import tools as toolkit  # noqa: E402
from company.loop import run_loop  # noqa: E402
from company.providers import ProviderError  # noqa: E402


class AasMcpClientTest(unittest.TestCase):
    """AdaptiveAIStudio が繋ぎに来たときの手順を、そのままなぞる。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root, self._settings = toolkit.ROOT, runner.SETTINGS_PATH
        toolkit.ROOT = self.tmp
        runner.SETTINGS_PATH = self.tmp / "settings.json"
        self.client = TestClient(server.app)
        self.n = 0

    def tearDown(self):
        toolkit.ROOT, runner.SETTINGS_PATH = self._root, self._settings

    def _rpc(self, method, params=None, session=None, notify=False):
        self.n += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            payload["id"] = self.n
        headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
        if session:
            headers["mcp-session-id"] = session
        return self.client.post("/mcp", content=json.dumps(payload), headers=headers)

    def test_handshake_then_list_then_call(self):
        init = self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "AdaptiveAIStudio", "version": "0.1.0"}})
        self.assertEqual(init.status_code, 200)
        self.assertEqual(init.json()["result"]["protocolVersion"], "2025-06-18")
        session = init.headers.get("mcp-session-id")
        self.assertTrue(session)

        note = self._rpc("notifications/initialized", session=session, notify=True)
        self.assertEqual(note.status_code, 202)

        listed = self._rpc("tools/list", {}, session=session).json()["result"]["tools"]
        names = {t["name"] for t in listed}
        self.assertEqual(names, {"list_roles", "start_cycle", "start_role", "get_run", "stop_run", "read_file"})
        self.assertIn("inputSchema", listed[0])

        called = self._rpc("tools/call", {"name": "list_roles", "arguments": {}}, session=session).json()["result"]
        self.assertFalse(called["isError"])
        self.assertIn("build", json.loads(called["content"][0]["text"])["cadences"])

        read = self._rpc("tools/call", {"name": "read_file", "arguments": {"path": "company.yaml"}}, session=session).json()["result"]
        self.assertIn("mission", read["content"][0]["text"])

    def test_tool_failure_is_is_error_not_rpc_error(self):
        r = self._rpc("tools/call", {"name": "get_run", "arguments": {"run_id": "nope"}}).json()
        self.assertIn("result", r)
        self.assertTrue(r["result"]["isError"])

    def test_unknown_method_is_rpc_error(self):
        r = self._rpc("resources/list").json()
        self.assertEqual(r["error"]["code"], -32601)

    def test_delete_closes_session_and_get_is_refused(self):
        self.assertEqual(self.client.delete("/mcp", headers={"mcp-session-id": "x"}).status_code, 204)
        self.assertEqual(self.client.get("/mcp").status_code, 405)

    def test_start_role_from_aas_runs_the_company(self):
        from tests.test_loop import FakeLLM
        llm = FakeLLM([{"text": "見ました"}])
        try:
            self.client.post("/api/settings", json={"provider": "openai_compatible", "base_url": llm.url, "model": "fake"})
            started = json.loads(self._rpc("tools/call", {"name": "start_role", "arguments": {"role": "ops"}}).json()["result"]["content"][0]["text"])
            run = runner.REGISTRY.get(started["id"])
            for _ in range(100):
                if run.status != "running":
                    break
                run.wait_for(len(run.events), 0.1)
        finally:
            llm.close()
        self.assertEqual(run.status, "done")


class FakeAas:
    """/api/send で受け付け、/api/events で返事を流す偽の AdaptiveAIStudio。"""

    def __init__(self, reply: str = "直しました", refuse: str | None = None):
        outer = self
        self.sent: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _json(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.startswith("/api/events/seq"):
                    return self._json(200, {"seq": 7})
                if self.path.startswith("/api/state"):
                    return self._json(200, {"model": "ollama:qwen3.5:9b"})
                if self.path.startswith("/api/events"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    cid = "c1"
                    for ev in [
                        {"type": "busy", "busy": True, "conversationId": cid},
                        {"type": "session", "conversationId": cid, "event": {"type": "text", "delta": reply[:3]}},
                        {"type": "session", "conversationId": "other", "event": {"type": "text", "delta": "無関係"}},
                        {"type": "session", "conversationId": cid, "event": {"type": "text", "delta": reply[3:]}},
                        {"type": "busy", "busy": False, "conversationId": cid},
                    ]:
                        self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                        self.wfile.flush()
                    return
                self._json(404, {})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.sent.append(json.loads(self.rfile.read(length)))
                if refuse:
                    return self._json(503, {"error": refuse})
                self._json(202, {"accepted": True, "conversationId": "c1", "loading": False})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class AasProviderTest(unittest.TestCase):
    def test_one_round_trip_collects_only_this_conversation(self):
        fake = FakeAas("直しました")
        try:
            p = aas.AasProvider(fake.url)
            turn = p.complete("決まり", [p.user_message("add を直して")], [], 0.2)
        finally:
            fake.close()
        self.assertEqual(turn.text, "直しました")
        self.assertEqual(turn.tool_calls, [])
        # 最初の往復には決まり（system）が添えられる
        self.assertTrue(fake.sent[0]["text"].startswith("決まり"))
        self.assertIn("add を直して", fake.sent[0]["text"])

    def test_runs_through_the_company_loop(self):
        fake = FakeAas("できました")
        events = []
        try:
            result = run_loop(aas.AasProvider(fake.url), "sys", "task", toolkit.toolset(False), events.append, role="builder")
        finally:
            fake.close()
        self.assertEqual(result.stopped_reason, "done")
        self.assertEqual(result.final_text, "できました")

    def test_refusal_is_explained(self):
        fake = FakeAas(refuse="モデルがありません")
        try:
            with self.assertRaises(ProviderError) as ctx:
                aas.AasProvider(fake.url).complete("s", [{"role": "user", "content": "x"}], [], 0.2)
        finally:
            fake.close()
        self.assertIn("モデルがありません", str(ctx.exception))

    def test_unreachable_is_explained(self):
        with self.assertRaises(ProviderError) as ctx:
            aas.AasProvider("http://127.0.0.1:9").list_models()
        self.assertIn("起動しているか", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
