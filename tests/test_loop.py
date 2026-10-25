"""ローカルLLM経路の end-to-end 検証。

OpenAI 互換APIを模した偽サーバーを立て、本物の OpenAICompatibleProvider と
本物のツールで run_loop を回す。モデルだけが台本通りに答える。
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

from company import tools as toolkit  # noqa: E402
from company.loop import run_loop  # noqa: E402
from company.providers import OpenAICompatibleProvider, ProviderError  # noqa: E402


class FakeLLM:
    """台本（script）に従って /chat/completions に答える偽のローカルLLM。"""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):  # 静かに
                pass

            def _send(self, code: int, body: dict):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.endswith("/models"):
                    self._send(200, {"data": [{"id": "fake-7b"}, {"id": "fake-3b"}]})
                else:
                    self._send(404, {})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append(json.loads(self.rfile.read(length)))
                if not outer.script:
                    self._send(500, {"error": "台本が尽きました"})
                    return
                step = outer.script.pop(0)
                message = {"role": "assistant", "content": step.get("text")}
                if "tool" in step:
                    name, args = step["tool"]
                    message["tool_calls"] = [{
                        "id": f"call_{len(outer.requests)}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }]
                self._send(200, {
                    "choices": [{"message": message}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                })

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class LocalLLMLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._root = toolkit.ROOT
        toolkit.ROOT = self.tmp
        self.events: list[dict] = []

    def tearDown(self):
        toolkit.ROOT = self._root

    def _run(self, script, can_write=True, **kw):
        llm = FakeLLM(script)
        try:
            provider = OpenAICompatibleProvider(llm.url, "fake-7b")
            result = run_loop(provider, "system", "task", toolkit.toolset(can_write),
                              self.events.append, role="builder", **kw)
        finally:
            llm.close()
        return result, llm

    def test_model_can_write_a_file_and_finish(self):
        result, llm = self._run([
            {"tool": ("write_file", {"path": "hello.txt", "content": "hi"})},
            {"text": "書きました"},
        ])
        self.assertEqual(result.stopped_reason, "done")
        self.assertEqual((self.tmp / "hello.txt").read_text(), "hi")
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.final_text, "書きました")
        # ツール結果が次のリクエストに tool メッセージとして入っていること
        second = llm.requests[1]["messages"]
        self.assertEqual(second[-1]["role"], "tool")
        self.assertIn("書き込みました", second[-1]["content"])

    def test_tools_are_advertised_in_openai_format(self):
        _, llm = self._run([{"text": "何もしない"}])
        names = {t["function"]["name"] for t in llm.requests[0]["tools"]}
        self.assertEqual(names, {"read_file", "list_files", "run_command", "write_file"})
        self.assertEqual(llm.requests[0]["messages"][0]["role"], "system")

    def test_read_only_roles_cannot_write(self):
        result, _ = self._run([
            {"tool": ("write_file", {"path": "x.txt", "content": "no"})},
            {"text": "done"},
        ], can_write=False)
        self.assertFalse((self.tmp / "x.txt").exists())
        unknown = [e for e in self.events if e["type"] == "tool_result"][0]
        self.assertIn("未知のツール", unknown["text"])

    def test_path_escape_is_refused(self):
        self._run([
            {"tool": ("read_file", {"path": "../../etc/passwd"})},
            {"text": "done"},
        ])
        res = [e for e in self.events if e["type"] == "tool_result"][0]
        self.assertIn("リポジトリの外", res["text"])

    def test_run_command_returns_exit_code_and_output(self):
        self._run([
            {"tool": ("run_command", {"command": f"{sys.executable} -c \"print('ok'); import sys; sys.exit(3)\""})},
            {"text": "done"},
        ])
        res = [e for e in self.events if e["type"] == "tool_result"][0]
        self.assertIn("exit code: 3", res["text"])
        self.assertIn("ok", res["text"])

    def test_max_turns_stops_the_loop(self):
        result, _ = self._run(
            [{"tool": ("list_files", {})}] * 5,
            max_turns=2,
        )
        self.assertEqual(result.stopped_reason, "max_turns")
        self.assertEqual(result.turns, 2)

    def test_stop_event_halts_before_next_turn(self):
        stop = threading.Event()
        stop.set()
        result, _ = self._run([{"text": "never"}], stop=stop)
        self.assertEqual(result.stopped_reason, "stopped")
        self.assertEqual(result.turns, 0)

    def test_server_error_becomes_error_event_not_crash(self):
        result, _ = self._run([])  # 台本なし → 500
        self.assertEqual(result.stopped_reason, "error")
        self.assertEqual(self.events[-1]["type"], "error")

    def test_unreachable_server_is_explained(self):
        provider = OpenAICompatibleProvider("http://127.0.0.1:9/v1", "x")
        with self.assertRaises(ProviderError) as ctx:
            provider.list_models()
        self.assertIn("起動しているか", str(ctx.exception))

    def test_list_models(self):
        llm = FakeLLM([])
        try:
            self.assertEqual(OpenAICompatibleProvider(llm.url, "-").list_models(), ["fake-3b", "fake-7b"])
        finally:
            llm.close()


if __name__ == "__main__":
    unittest.main()
