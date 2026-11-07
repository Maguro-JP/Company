"""内蔵エンジンの検証。

解釈ロジックは常に走る。実推論は data/models に動作確認用モデルがあるときだけ走る
（無ければ skip。CI や初回クローンで落ちないように）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from company import local_llm, tools as toolkit  # noqa: E402
from company.loop import run_loop  # noqa: E402
from company.providers import ToolSpec  # noqa: E402

TINY = local_llm.MODELS_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"


class ParseTest(unittest.TestCase):
    def test_tagged_call(self):
        text, call = local_llm.parse_tool_call('<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>')
        self.assertEqual(text, "")
        self.assertEqual(call.name, "read_file")
        self.assertEqual(call.arguments, {"path": "a.py"})

    def test_missing_close_tag_is_tolerated(self):
        """stop トークンで切ると閉じタグが無い。それでも解釈できること。"""
        _, call = local_llm.parse_tool_call('<tool_call>{"name": "list_files", "arguments": {}}')
        self.assertEqual(call.name, "list_files")

    def test_text_before_call_is_kept(self):
        text, call = local_llm.parse_tool_call('まず一覧を見ます。\n<tool_call>{"name": "list_files", "arguments": {}}</tool_call>')
        self.assertEqual(text, "まず一覧を見ます。")
        self.assertIsNotNone(call)

    def test_json_fence_fallback(self):
        _, call = local_llm.parse_tool_call('```json\n{"name": "read_file", "arguments": {"path": "x"}}\n```')
        self.assertEqual(call.name, "read_file")

    def test_plain_text_has_no_call(self):
        text, call = local_llm.parse_tool_call("完了しました。")
        self.assertEqual(text, "完了しました。")
        self.assertIsNone(call)

    def test_broken_json_is_text(self):
        text, call = local_llm.parse_tool_call('<tool_call>{"name": "x", </tool_call>')
        self.assertIsNone(call)

    def test_string_arguments_are_decoded(self):
        _, call = local_llm.parse_tool_call('<tool_call>{"name": "f", "arguments": "{\\"a\\": 1}"}</tool_call>')
        self.assertEqual(call.arguments, {"a": 1})


class InstructionsTest(unittest.TestCase):
    def test_lists_tools_with_required_marking(self):
        spec = ToolSpec("read_file", "読む", {"type": "object", "properties": {"path": {"description": "パス"}, "n": {"type": "integer"}}, "required": ["path"]})
        text = local_llm.tool_instructions([spec])
        self.assertIn("read_file(path: パス, n?: integer)", text)
        self.assertIn("<tool_call>", text)

    def test_empty_when_no_tools(self):
        self.assertEqual(local_llm.tool_instructions([]), "")


class FakeEngine:
    """llama.cpp の create_chat_completion を模す。stream=True では塊を順に返す。"""

    def __init__(self, text: str):
        self.text = text
        self.calls: list[dict] = []

    def create_chat_completion(self, **kw):
        self.calls.append(kw)
        if kw.get("stream"):
            def gen():
                for i in range(0, len(self.text), 3):
                    yield {"choices": [{"delta": {"content": self.text[i:i + 3]}}]}
                yield {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}
            return gen()
        return {"choices": [{"message": {"content": self.text}}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}


class StreamingTest(unittest.TestCase):
    def setUp(self):
        self._engine = local_llm._engine
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "m.gguf").write_bytes(b"GGUF")
        self._root = toolkit.ROOT
        toolkit.ROOT = self.tmp

    def tearDown(self):
        local_llm._engine = self._engine
        toolkit.ROOT = self._root

    def _provider(self, text):
        engine = FakeEngine(text)
        local_llm._engine = lambda *a, **k: engine
        p = local_llm.BuiltinProvider("m.gguf")
        p.path = self.tmp / "m.gguf"
        return p, engine

    def test_deltas_are_streamed_when_a_listener_is_set(self):
        p, engine = self._provider("こんにちは、世界")
        got = []
        p.on_delta = got.append
        turn = p.complete("s", [p.user_message("hi")], [], 0.2)
        self.assertTrue(engine.calls[0]["stream"])
        self.assertEqual("".join(got), "こんにちは、世界")
        self.assertEqual(turn.text, "こんにちは、世界")
        self.assertEqual(turn.usage, {"input": 9, "output": 4})

    def test_usage_is_estimated_when_the_stream_has_none(self):
        p, engine = self._provider("abcdefgh")
        engine.create_chat_completion = lambda **kw: iter([{"choices": [{"delta": {"content": "abcdefgh"}}]}])
        p.on_delta = lambda d: None
        turn = p.complete("s", [p.user_message("hi")], [], 0.2)
        self.assertEqual(turn.usage["output"], 2)
        self.assertGreater(turn.usage["input"], 0)

    def test_without_listener_it_does_not_stream(self):
        p, engine = self._provider("x")
        p.complete("s", [p.user_message("hi")], [], 0.2)
        self.assertNotIn("stream", engine.calls[0])

    def test_tool_call_still_parsed_from_streamed_text(self):
        p, _ = self._provider('<tool_call>{"name": "list_files", "arguments": {}}')
        p.on_delta = lambda d: None
        turn = p.complete("s", [p.user_message("hi")], [], 0.2)
        self.assertEqual(turn.tool_calls[0].name, "list_files")

    def test_loop_emits_delta_events_before_the_final_text(self):
        p, _ = self._provider("終わりました")
        events = []
        run_loop(p, "s", "t", toolkit.toolset(False), events.append, max_turns=2, role="ops")
        kinds = [e["type"] for e in events]
        self.assertIn("delta", kinds)
        self.assertLess(kinds.index("delta"), kinds.index("text"))
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "delta"), "終わりました")


class CatalogTest(unittest.TestCase):
    def test_catalog_entries_resolve_to_model_dir(self):
        for entry in local_llm.CATALOG:
            self.assertEqual(local_llm.model_path(entry["id"]), local_llm.MODELS_DIR / entry["file"])

    def test_unknown_gguf_name_is_allowed(self):
        self.assertEqual(local_llm.model_path("custom.gguf"), local_llm.MODELS_DIR / "custom.gguf")

    def test_list_models_marks_download_state(self):
        ids = {m["id"]: m for m in local_llm.list_models()}
        self.assertIn("qwen2.5-coder-7b", ids)
        self.assertIn("downloaded", ids["qwen2.5-coder-7b"])

    def test_missing_model_gives_actionable_error(self):
        from company.providers import ProviderError
        p = local_llm.BuiltinProvider("qwen2.5-coder-7b")
        if p.path.exists():
            self.skipTest("モデルが実在する")
        with self.assertRaises(ProviderError) as ctx:
            p.complete("s", [p.user_message("hi")], [], 0.2)
        self.assertIn("ダウンロード", str(ctx.exception))


@unittest.skipUnless(TINY.exists(), "動作確認用モデルが data/models にない")
class RealInferenceTest(unittest.TestCase):
    """本物の llama.cpp で、本物のツールを、0.5B モデルに呼ばせる。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "product").mkdir()
        (self.tmp / "product" / "detect.py").write_text("print('hello')\n")
        self._root = toolkit.ROOT
        toolkit.ROOT = self.tmp
        self.events = []

    def tearDown(self):
        toolkit.ROOT = self._root

    def test_model_calls_a_tool_through_the_loop(self):
        provider = local_llm.BuiltinProvider("qwen2.5-0.5b", n_ctx=4096, n_gpu_layers=0)
        result = run_loop(
            provider, "あなたは開発者です。", "product/ 配下のファイルを list_files で列挙し、終わったら一言で報告してください。",
            toolkit.toolset(can_write=False), self.events.append, max_turns=4, temperature=0.0, role="t",
        )
        names = [e["name"] for e in self.events if e["type"] == "tool_call"]
        self.assertIn("list_files", names, msg=f"events={self.events}")
        self.assertIn(result.stopped_reason, ("done", "max_turns"))
        self.assertGreater(result.usage["output"], 0)


if __name__ == "__main__":
    unittest.main()
