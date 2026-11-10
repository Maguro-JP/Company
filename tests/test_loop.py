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
            {"tool": ("run_command", {"command": f"\"{sys.executable}\" -c \"print('ok'); import sys; sys.exit(3)\""})},
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

    def test_token_budget_stops_the_loop(self):
        """偽サーバーは1ターン 15 トークン。上限 40 なら 3 ターン目で止まる。"""
        result, _ = self._run([{"tool": ("list_files", {})}] * 10, max_total_tokens=40)
        self.assertEqual(result.stopped_reason, "budget")
        self.assertEqual(result.turns, 3)
        self.assertGreaterEqual(result.usage["input"] + result.usage["output"], 40)
        self.assertIn("トークン上限", self.events[-1]["text"])

    def test_zero_budget_means_unlimited(self):
        result, _ = self._run([{"text": "done"}], max_total_tokens=0)
        self.assertEqual(result.stopped_reason, "done")

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
        self.assertIn("起動していないか", str(ctx.exception))

    def test_list_models(self):
        llm = FakeLLM([])
        try:
            self.assertEqual(OpenAICompatibleProvider(llm.url, "-").list_models(), ["fake-3b", "fake-7b"])
        finally:
            llm.close()


if __name__ == "__main__":
    unittest.main()


class MalformedToolCallTest(unittest.TestCase):
    """<tool_call> と書いたのに読めない返答は本文にせず、言い直させる。2 回続いたら error で止める。"""

    class _P:
        name = "fake"

        def __init__(self, texts):
            self.texts = list(texts)
            self.seen = []

        def user_message(self, text):
            return {"role": "user", "content": text}

        def tool_results(self, results):
            return []

        def complete(self, system, history, tools, temperature):
            from company.providers import Turn
            self.seen.append(history[-1]["content"])
            text = self.texts.pop(0)
            return Turn(text=text, tool_calls=[], assistant_message={"role": "assistant", "content": text}, usage={"input": 1, "output": 1})

    def test_reprompts_then_gives_up(self):
        p = self._P(['<tool_call>{"name": "x", "arguments": {"a":', '<tool_call>{"name": "x"'])
        events = []
        r = run_loop(p, "sys", "task", {}, events.append, max_turns=5, role="ceo")
        self.assertEqual(r.stopped_reason, "error")
        self.assertEqual(r.final_text, "")
        self.assertIn("言い直させます", " ".join(e.get("text", "") for e in events if e["type"] == "status"))
        self.assertIn("閉じタグ", p.seen[1])
        self.assertFalse(any(e["type"] == "text" for e in events))

    def test_reprompt_then_good_answer(self):
        p = self._P(['<tool_call>{"name": "x", "arguments": {"a":', "会社は準備中です。"])
        events = []
        r = run_loop(p, "sys", "task", {}, events.append, max_turns=5, role="ceo")
        self.assertEqual(r.stopped_reason, "done")
        self.assertEqual(r.final_text, "会社は準備中です。")


class ResultClipTest(unittest.TestCase):
    """小さいコンテキストでは道具の結果を切って模型に返す。表示（tool_result の出来事）は別に 1500 文字で切る。"""

    def test_result_chars_depends_on_builtin_context(self):
        from company import runner
        self.assertEqual(runner.result_chars_for({"provider": "builtin", "context": 4096}), 2456)
        self.assertEqual(runner.result_chars_for({"provider": "builtin", "context": 8192}), 4914)
        self.assertEqual(runner.result_chars_for({"provider": "anthropic", "context": 4096}), 0)

    def test_long_tool_output_is_clipped_for_the_model(self):
        from company.loop import run_loop
        from company.providers import ToolCall

        class P:
            def __init__(self):
                self.seen = []

            def user_message(self, text):
                return {"role": "user", "content": text}

            def tool_results(self, results):
                self.seen.extend(out for _c, out in results)
                return [{"role": "tool", "content": out} for _c, out in results]

            def complete(self, system, history, tools, temperature=0.2):
                from company.providers import Turn as ProviderResult
                if len(history) == 1:
                    return ProviderResult(text="", tool_calls=[ToolCall(id="1", name="big", arguments={})], usage={})
                return ProviderResult(text="終わり", tool_calls=[], usage={})

        p = P()
        tools = {"big": ({"name": "big", "description": "", "parameters": {"type": "object", "properties": {}}}, lambda a: "x" * 10000)}
        run_loop(p, "s", "t", tools, lambda e: None, max_turns=3, role="ceo", result_chars=500)
        self.assertEqual(len(p.seen), 1)
        self.assertLess(len(p.seen[0]), 600)
        self.assertIn("省きました", p.seen[0])

