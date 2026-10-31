"""実行の管理。設定の永続化、サイクルの組み立て、実行中のランの保持。

UI（server.py）とCLI（run.py）の両方がここを使う。
"""

from __future__ import annotations

import csv
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import context as context_builder
from . import tools as toolkit
from . import config as config_module
from .config import AGENTS_DIR, DATA_DIR, DECISIONS_DIR, Config, load_agent, load_config
from .loop import Event, LoopResult, run_loop
from .providers import ProviderError, make_provider

SETTINGS_PATH = DATA_DIR / "settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": "builtin",
    "base_url": "http://localhost:11434/v1",
    "model": "",
    "api_key": "",
    "temperature": 0.2,
    "max_turns": 60,
    "max_tokens_per_run": 0,   # 1つの役職の実行あたりの入力＋出力トークン上限。0 = 無制限
    "context": 8192,     # 内蔵エンジンのコンテキスト長
    "gpu_layers": -1,    # 内蔵エンジン。-1 = GPU があれば全部載せる
}

# 実装系の役職だけ書き込みツールを持つ
WRITER_ROLES = {"builder", "cto", "cmo"}

BUILD_TASK = """
# 今回のタスク

product/ にあるプロダクトを、顧客にとって価値が1つ増える状態にしてください。
何を作るかはあなたが決めます。ただし、以下を満たすまでが1タスクです。

1. list_files と read_file で現状を把握する
2. 実装する（write_file）
3. product/tests/ にテストを足す
4. run_command で `cd product && python -m unittest discover -s tests` を実行し、通ることを確認する
5. 通ってから run_command で `git add -A && git commit -m "..."` する（落ちたままコミットしない）

大きく作らないこと。1サイクルで1つ、確実に動くものを足す方が価値が高い。
既にあるものを壊さないこと。終わったら、何を足したかを3行以内で報告してください。
""".strip()

REVIEW_TASK = """
# 今回のタスク

リポジトリの現状を実際に読んで確認し、あなたの役割定義の形式で報告してください。
推測で書かず、read_file で確かめること。テストの状態は run_command で実際に実行して確認すること。
""".strip()


def load_settings() -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_PATH.exists():
        try:
            settings.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return settings


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in patch:
            settings[key] = patch[key]
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings


def list_roles() -> list[dict[str, str]]:
    roles = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        agent = load_agent(path.stem)
        roles.append({"role": agent.role, "title": agent.title, "writes": agent.role in WRITER_ROLES})
    return roles


@dataclass
class Run:
    id: str
    kind: str                 # cycle | role
    target: str               # cadence 名 or 役職名
    started_at: str
    events: list[Event] = field(default_factory=list)
    status: str = "running"   # running | done | error | stopped
    stop: threading.Event = field(default_factory=threading.Event)
    _cv: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, event: Event) -> None:
        with self._cv:
            event.setdefault("at", datetime.now().strftime("%H:%M:%S"))
            self.events.append(event)
            self._cv.notify_all()

    def finish(self, status: str) -> None:
        with self._cv:
            self.status = status
            self._cv.notify_all()

    def wait_for(self, index: int, timeout: float) -> list[Event]:
        """index 以降のイベントを返す。無ければ timeout まで待つ。"""
        with self._cv:
            if len(self.events) <= index and self.status == "running":
                self._cv.wait(timeout)
            return self.events[index:]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "started_at": self.started_at,
            "status": self.status,
            "events": len(self.events),
        }


class Registry:
    def __init__(self):
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def create(self, kind: str, target: str) -> Run:
        run = Run(id=uuid.uuid4().hex[:8], kind=kind, target=target,
                  started_at=datetime.now().isoformat(timespec="seconds"))
        with self._lock:
            self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def all(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)


REGISTRY = Registry()


def _task_for(role: str, shared: str, instruction: str | None) -> str:
    base = BUILD_TASK if role in WRITER_ROLES else REVIEW_TASK
    if instruction:
        base = f"{base}\n\n# オーナーからの指示\n\n{instruction.strip()}\n\nこの指示を最優先してください。"
    return f"{shared}\n\n{base}"


def run_role(
    run: Run, config: Config, settings: dict[str, Any], role: str, cadence: str,
    instruction: str | None = None,
) -> LoopResult | None:
    agent = load_agent(role)
    shared = context_builder.build(config, cadence)
    can_write = role in WRITER_ROLES and bool(config.autonomy.get("write_code", True))

    run.emit({"type": "role_start", "role": role, "title": agent.title,
              "model": settings.get("model"), "writes": can_write})
    try:
        provider = make_provider(settings)
    except ProviderError as exc:
        run.emit({"type": "error", "role": role, "text": str(exc)})
        return None

    result = run_loop(
        provider=provider,
        system=agent.instructions,
        task=_task_for(role, shared, instruction),
        tools=toolkit.toolset(can_write),
        emit=run.emit,
        max_turns=int(settings.get("max_turns", 60)),
        temperature=float(settings.get("temperature", 0.2)),
        stop=run.stop,
        role=role,
        max_total_tokens=int(settings.get("max_tokens_per_run", 0) or 0),
    )
    run.emit({"type": "role_end", "role": role, "turns": result.turns,
              "tool_calls": result.tool_calls, "usage": result.usage, "reason": result.stopped_reason})
    try:
        append_run_log(run, role, settings, result)
    except OSError as exc:  # 記録が書けなくても実行は成立している
        run.emit({"type": "status", "role": role, "text": f"実績を記録できませんでした: {exc}"})
    return result


RUN_LOG_FIELDS = ["at", "run_id", "kind", "target", "role", "provider", "model",
                  "turns", "tool_calls", "input_tokens", "output_tokens", "reason"]


def append_run_log(run: Run, role: str, settings: dict[str, Any], result: LoopResult) -> None:
    """役職の実行を1行で残す。後から「実際にどれだけ回ったか」を数字で見るため。"""
    path = config_module.RUNS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RUN_LOG_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow({
            "at": datetime.now().isoformat(timespec="seconds"),
            "run_id": run.id, "kind": run.kind, "target": run.target, "role": role,
            "provider": settings.get("provider", ""), "model": settings.get("model", ""),
            "turns": result.turns, "tool_calls": result.tool_calls,
            "input_tokens": result.usage.get("input", 0), "output_tokens": result.usage.get("output", 0),
            "reason": result.stopped_reason,
        })


def write_record(config: Config, cadence: str, reports: list[tuple[str, str]]) -> Path:
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    existing = len(list(DECISIONS_DIR.glob(f"{today}-{cadence}*.md")))
    path = DECISIONS_DIR / f"{today}-{cadence}{'-' + str(existing + 1) if existing else ''}.md"
    lines = [f"# {today} {cadence} サイクル", "", f"- 会社: {config.company.get('name', '未設定')}", ""]
    for title, body in reports:
        lines += [f"## {title}", "", body or "（出力なし）", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _execute(run: Run, roles: list[str], cadence: str, instruction: str | None) -> None:
    config = load_config()
    settings = load_settings()
    reports: list[tuple[str, str]] = []
    status = "done"
    for role in roles:
        if run.stop.is_set():
            status = "stopped"
            break
        result = run_role(run, config, settings, role, cadence, instruction)
        if result is None:
            status = "error"
            break
        if result.stopped_reason == "stopped":
            status = "stopped"
            break
        reports.append((load_agent(role).title, result.final_text))
    if reports and run.kind == "cycle" and status == "done":
        path = write_record(config, cadence, reports)
        run.emit({"type": "status", "role": "", "text": f"記録を書き出しました: {path.relative_to(DATA_DIR.parent)}"})
    run.finish(status)


def start_cycle(cadence: str, instruction: str | None = None) -> Run:
    config = load_config()
    roles = config.roles_for(cadence)
    if not roles:
        raise ValueError(f"cadence '{cadence}' が company.yaml にありません。")
    run = REGISTRY.create("cycle", cadence)
    threading.Thread(target=_execute, args=(run, roles, cadence, instruction), daemon=True).start()
    return run


def start_role(role: str, instruction: str | None = None) -> Run:
    load_agent(role)  # 存在確認
    run = REGISTRY.create("role", role)
    threading.Thread(target=_execute, args=(run, [role], "manual", instruction), daemon=True).start()
    return run
