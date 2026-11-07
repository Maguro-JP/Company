"""プロバイダ非依存のエージェントループ。

モデルにツールを渡し、呼ばれたら実行して結果を返し、呼ばれなくなるまで回す。
途中経過はコールバックで外に出す（UIのストリーム表示に使う）。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .providers import Provider, ProviderError, ToolCall
from .tools import ToolFn, ToolSpec

Event = dict[str, Any]
Emit = Callable[[Event], None]


@dataclass
class LoopResult:
    final_text: str
    turns: int
    tool_calls: int
    usage: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0})
    stopped_reason: str = "done"  # done | max_turns | budget | stopped | error


def _args_preview(args: dict[str, Any], limit: int = 200) -> str:
    try:
        text = json.dumps(args, ensure_ascii=False)
    except TypeError:
        text = str(args)
    return text if len(text) <= limit else text[:limit] + "…"


def run_loop(
    provider: Provider,
    system: str,
    task: str,
    tools: dict[str, tuple[ToolSpec, ToolFn]],
    emit: Emit,
    max_turns: int = 60,
    temperature: float = 0.2,
    stop: threading.Event | None = None,
    role: str = "",
    max_total_tokens: int = 0,
) -> LoopResult:
    """max_total_tokens は入力＋出力の合計の上限。0 なら無制限。
    超えたターンで止める（途中では切れないので、上限を少し超えて止まる）。"""
    history: list[Any] = [provider.user_message(task)]
    specs = [spec for spec, _ in tools.values()]
    turn_box = {"n": 0}

    def on_delta(delta: str) -> None:
        emit({"type": "delta", "role": role, "text": delta, "turn": turn_box["n"]})

    # 流せるプロバイダ（内蔵エンジン）だけが見る。他は属性を無視する
    provider.on_delta = on_delta  # type: ignore[attr-defined]
    usage = {"input": 0, "output": 0}
    tool_calls_total = 0
    final_text = ""

    for turn in range(1, max_turns + 1):
        if stop is not None and stop.is_set():
            emit({"type": "status", "role": role, "text": "停止しました"})
            return LoopResult(final_text, turn - 1, tool_calls_total, usage, "stopped")

        started = time.time()
        turn_box["n"] = turn
        try:
            result = provider.complete(system, history, specs, temperature)
        except ProviderError as exc:
            emit({"type": "error", "role": role, "text": str(exc)})
            return LoopResult(final_text, turn, tool_calls_total, usage, "error")

        usage["input"] += result.usage.get("input", 0)
        usage["output"] += result.usage.get("output", 0)
        history.append(result.assistant_message)

        spent = usage["input"] + usage["output"]
        if max_total_tokens and spent >= max_total_tokens:
            if result.text:
                final_text = result.text
                emit({"type": "text", "role": role, "text": result.text, "turn": turn})
            emit({"type": "status", "role": role,
                  "text": f"トークン上限 {max_total_tokens} に達したので止めました（使用 {spent}）"})
            return LoopResult(final_text, turn, tool_calls_total, usage, "budget")

        if result.text:
            final_text = result.text
            emit({"type": "text", "role": role, "text": result.text, "turn": turn})

        if not result.tool_calls:
            emit({"type": "status", "role": role, "text": f"完了（{turn} ターン, {tool_calls_total} ツール呼び出し）"})
            return LoopResult(final_text, turn, tool_calls_total, usage, "done")

        results: list[tuple[ToolCall, str]] = []
        for call in result.tool_calls:
            tool_calls_total += 1
            emit({"type": "tool_call", "role": role, "name": call.name, "args": _args_preview(call.arguments), "turn": turn})
            entry = tools.get(call.name)
            if entry is None:
                output = f"未知のツールです: {call.name}。使えるのは {', '.join(tools)} です。"
            else:
                _, fn = entry
                try:
                    output = fn(call.arguments)
                except (KeyError, TypeError) as exc:
                    output = f"引数が不正です: {exc}"
                except PermissionError as exc:
                    output = str(exc)
                except Exception as exc:  # ツール内部の失敗はモデルに返して続行させる
                    output = f"ツール実行エラー: {type(exc).__name__}: {exc}"
            results.append((call, output))
            emit({
                "type": "tool_result",
                "role": role,
                "name": call.name,
                "text": output if len(output) <= 1500 else output[:1500] + "\n…",
                "ms": int((time.time() - started) * 1000),
            })
        history.extend(provider.tool_results(results))

    emit({"type": "status", "role": role, "text": f"ターン上限 {max_turns} に達しました"})
    return LoopResult(final_text, max_turns, tool_calls_total, usage, "max_turns")
