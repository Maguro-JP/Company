"""Webhook 通知の検証。偽の受け口で届くこと、無いときは送らないこと、落ちてもランは成立すること。"""

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

from company import config as config_module  # noqa: E402
from company import notify, runner, server  # noqa: E402
from company import tools as toolkit  # noqa: E402
from tests.test_loop import FakeLLM  # noqa: E402


class FakeWebhook:
    def __init__(self, status: int = 200):
        outer = self
        self.received: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                outer.received.append(json.loads(self.rfile.read(n)))
                self.send_response(status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class NotifyUnitTest(unittest.TestCase):
    def test_should_notify_rules(self):
        self.assertFalse(notify.should_notify({"webhook_url": ""}, "done"))
        self.assertTrue(notify.should_notify({"webhook_url": "http://x"}, "done"))
        self.assertFalse(notify.should_notify({"webhook_url": "http://x", "notify_on": "error"}, "done"))
        self.assertTrue(notify.should_notify({"webhook_url": "http://x", "notify_on": "error"}, "error"))

    def test_message_has_status_tokens_and_excerpt(self):
        msg = notify.build_message({"id": "ab12", "kind": "cycle", "target": "build"}, "done",
                                   [("CEO", "x" * 500)], {"input": 10, "output": 5})
        self.assertIn("build が完了", msg)
        self.assertIn("入力 10 / 出力 5", msg)
        self.assertIn("…", msg)

    def test_send_payload_fits_slack_and_discord(self):
        hook = FakeWebhook()
        try:
            notify.send(hook.url, "こんにちは")
        finally:
            hook.close()
        self.assertEqual(hook.received[0], {"text": "こんにちは", "content": "こんにちは"})

    def test_send_raises_on_http_error_and_unreachable(self):
        hook = FakeWebhook(status=500)
        try:
            with self.assertRaises(RuntimeError):
                notify.send(hook.url, "x")
        finally:
            hook.close()
        with self.assertRaises(RuntimeError):
            notify.send("http://127.0.0.1:9/hook", "x")


class NotifyThroughRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root, self._settings, self._runs = toolkit.ROOT, runner.SETTINGS_PATH, config_module.RUNS_PATH
        toolkit.ROOT = self.tmp
        runner.SETTINGS_PATH = self.tmp / "settings.json"
        config_module.RUNS_PATH = self.tmp / "runs.csv"
        self.client = TestClient(server.app)

    def tearDown(self):
        toolkit.ROOT, runner.SETTINGS_PATH, config_module.RUNS_PATH = self._root, self._settings, self._runs

    def _run_role(self, extra_settings):
        llm = FakeLLM([{"text": "見ました"}])
        try:
            self.client.post("/api/settings", json={"provider": "openai_compatible", "base_url": llm.url, "model": "fake", **extra_settings})
            started = self.client.post("/api/runs", json={"kind": "role", "target": "ops"}).json()
            run = runner.REGISTRY.get(started["id"])
            for _ in range(100):
                if run.status != "running":
                    break
                run.wait_for(len(run.events), 0.1)
        finally:
            llm.close()
        return run

    def test_webhook_receives_the_run_result(self):
        hook = FakeWebhook()
        try:
            run = self._run_role({"webhook_url": hook.url})
        finally:
            hook.close()
        self.assertEqual(run.status, "done")
        self.assertEqual(len(hook.received), 1)
        self.assertIn(run.id, hook.received[0]["text"])
        self.assertIn("見ました", hook.received[0]["text"])
        self.assertIn("Webhook に知らせました", [e.get("text") for e in run.events])

    def test_unreachable_webhook_does_not_fail_the_run(self):
        run = self._run_role({"webhook_url": "http://127.0.0.1:9/hook"})
        self.assertEqual(run.status, "done")
        self.assertTrue(any("知らせられませんでした" in (e.get("text") or "") for e in run.events))

    def test_no_webhook_means_no_notice(self):
        run = self._run_role({"webhook_url": ""})
        self.assertFalse(any("Webhook" in (e.get("text") or "") for e in run.events))


if __name__ == "__main__":
    unittest.main()
