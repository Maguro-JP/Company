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
from . import notify
from . import partners, workstyle
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
    "roles": {},         # 役職ごとの上書き {"ceo": {"provider": "...", "model": "...", "api_key": "..."}}
    "webhook_url": "",         # Slack / Discord の incoming webhook。空なら知らせない
    "notify_on": "always",     # always | error
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
    if not isinstance(settings.get("roles"), dict):
        settings["roles"] = {}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings


ROLE_OVERRIDE_KEYS = ("provider", "model", "api_key", "base_url", "temperature", "max_turns", "max_tokens_per_run")


def settings_for_role(settings: dict[str, Any], role: str) -> dict[str, Any]:
    """役職ごとの上書きを被せる。空の値は上書きしない（「全体に従う」の意味）。"""
    merged = dict(settings)
    override = (settings.get("roles") or {}).get(role) or {}
    for key in ROLE_OVERRIDE_KEYS:
        value = override.get(key)
        if value not in (None, ""):
            merged[key] = value
    return merged


def list_roles() -> list[dict[str, str]]:
    roles = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        agent = load_agent(path.stem)
        roles.append({"role": agent.role, "title": agent.title, "writes": agent.role in WRITER_ROLES})
    return roles


RUNS_DIR = DATA_DIR / "runs"


@dataclass
class Run:
    id: str
    kind: str                 # cycle | role
    target: str               # cadence 名 or 役職名
    started_at: str
    events: list[Event] = field(default_factory=list)
    status: str = "running"   # running | done | error | stopped
    instruction: str = ""
    stop: threading.Event = field(default_factory=threading.Event)
    _cv: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, event: Event) -> None:
        with self._cv:
            event.setdefault("at", datetime.now().strftime("%H:%M:%S"))
            self.events.append(event)
            self._cv.notify_all()
        # 出来事は 1 行ずつ追記する。起動し直しても履歴が読めるように（画面の「履歴」が読む）
        try:
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            with (RUNS_DIR / f"{self.id}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def finish(self, status: str) -> None:
        with self._cv:
            self.status = status
            self._cv.notify_all()
        try:
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            (RUNS_DIR / f"{self.id}.json").write_text(json.dumps(self.summary(), ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def final_texts(self) -> list[dict[str, str]]:
        """役職ごとの最後の text（成果の要約）。履歴の一覧に出す。"""
        out: dict[str, str] = {}
        for ev in self.events:
            if ev.get("type") == "text" and ev.get("role"):
                out[ev["role"]] = str(ev.get("text", ""))
        return [{"role": r, "text": t} for r, t in out.items()]

    def wait_for(self, index: int, timeout: float) -> list[Event]:
        """index 以降のイベントを返す。無ければ timeout まで待つ。"""
        with self._cv:
            if len(self.events) <= index and self.status == "running":
                self._cv.wait(timeout)
            return self.events[index:]

    def summary(self) -> dict[str, Any]:
        usage = {"input": 0, "output": 0}
        turns = 0
        for ev in self.events:
            if ev.get("type") == "role_end":
                usage["input"] += int((ev.get("usage") or {}).get("input", 0) or 0)
                usage["output"] += int((ev.get("usage") or {}).get("output", 0) or 0)
                turns += int(ev.get("turns", 0) or 0)
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "started_at": self.started_at,
            "status": self.status,
            "events": len(self.events),
            "instruction": self.instruction,
            "turns": turns,
            "usage": usage,
            "results": self.final_texts(),
        }


class Registry:
    def __init__(self):
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def create(self, kind: str, target: str, instruction: str | None = None) -> Run:
        run = Run(id=uuid.uuid4().hex[:8], kind=kind, target=target,
                  started_at=datetime.now().isoformat(timespec="seconds"), instruction=instruction or "")
        with self._lock:
            self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def all(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)

    def history(self, limit: int = 200) -> list[dict[str, Any]]:
        """今の実行と、記録に残っている過去の実行を合わせて新しい順に。"""
        seen = {r.id: r.summary() for r in self._runs.values()}
        if RUNS_DIR.is_dir():
            for path in RUNS_DIR.glob("*.json"):
                if path.stem in seen:
                    continue
                try:
                    seen[path.stem] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
        return sorted(seen.values(), key=lambda r: r.get("started_at", ""), reverse=True)[:limit]

    def load(self, run_id: str) -> dict[str, Any] | None:
        """記録から 1 件（出来事つき）。今の実行なら REGISTRY のものを使う。"""
        run = self._runs.get(run_id)
        if run is not None:
            return {**run.summary(), "events": run.events}
        meta = RUNS_DIR / f"{run_id}.json"
        log = RUNS_DIR / f"{run_id}.jsonl"
        if not meta.exists() and not log.exists():
            return None
        try:
            summary = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {"id": run_id, "status": "error"}
        except json.JSONDecodeError:
            summary = {"id": run_id, "status": "error"}
        events: list[dict[str, Any]] = []
        if log.exists():
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return {**summary, "events": events}

    def delete(self, run_id: str) -> bool:
        if run_id in self._runs and self._runs[run_id].status == "running":
            return False
        self._runs.pop(run_id, None)
        removed = False
        for path in (RUNS_DIR / f"{run_id}.json", RUNS_DIR / f"{run_id}.jsonl"):
            if path.exists():
                path.unlink()
                removed = True
        return removed


REGISTRY = Registry()


INSTRUCTED_TASK = """
# オーナーの指示（これだけをやる）

{instruction}

上の指示をそのまま行ってください。指示に無いこと（別の機能を足す、方針を決め直す、提案で止める）はしないでください。
手順は、必要なら list_files と read_file で現状を見てから、指示の通りに作り、テストが要るなら run_command で通し、
終わったら何をしたかを 3 行以内で報告してください。指示が読むだけのものなら、読んだ結果だけを報告してください。
""".strip()


def _task_for(role: str, shared: str, instruction: str | None) -> str:
    """役職の仕事の文。オーナーの指示があるときは、それを先頭に置き、それだけをやらせる。
    末尾に付けると、長い文脈の後ろで小さい模型が指示を無視した（ユーザーの機械で起きた）。"""
    if instruction:
        return f"{INSTRUCTED_TASK.format(instruction=instruction.strip())}\n\n# 参考（会社の現状。指示に関係ある所だけ見る）\n\n{shared}"
    base = BUILD_TASK if role in WRITER_ROLES else REVIEW_TASK
    return f"{shared}\n\n{base}"


def _system_for(agent_instructions: str, settings: dict[str, Any], instructed: bool) -> str:
    """役職の system。オーナーの指示があるときは、その旨を一番先に置く（役職の定義より上）。"""
    ctx = int(settings.get("context", 8192) or 8192)
    head = ("オーナーの指示が最優先です。役職の定義や会社の現状より、この後の「オーナーの指示」を優先し、指示に無いことはしないでください。\n\n"
            if instructed else "")
    return f"{head}{workstyle.build(max_chars=workstyle.budget_for(ctx))}\n\n{agent_instructions}"


def run_role(
    run: Run, config: Config, settings: dict[str, Any], role: str, cadence: str,
    instruction: str | None = None,
) -> LoopResult | None:
    agent = load_agent(role)
    settings = settings_for_role(settings, role)
    shared = context_builder.build(config, cadence)
    can_write = role in WRITER_ROLES and bool(config.autonomy.get("write_code", True))

    run.emit({"type": "role_start", "role": role, "title": agent.title,
              "provider": settings.get("provider"), "model": settings.get("model"), "writes": can_write})
    try:
        provider = make_provider(settings)
    except ProviderError as exc:
        run.emit({"type": "error", "role": role, "text": str(exc)})
        return None

    result = run_loop(
        provider=provider,
        system=_system_for(agent.instructions, settings, bool(instruction)),
        task=_task_for(role, shared, instruction),
        tools={**toolkit.toolset(can_write), **partners.toolset()},
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
    try:
        config = load_config()
        settings = load_settings()
        reports: list[tuple[str, str]] = []
        usage = {"input": 0, "output": 0}
        status = "done"
        for role in roles:
            if run.stop.is_set():
                status = "stopped"
                break
            result = run_role(run, config, settings, role, cadence, instruction)
            if result is None:
                status = "error"
                break
            usage["input"] += result.usage.get("input", 0)
            usage["output"] += result.usage.get("output", 0)
            if result.stopped_reason == "stopped":
                status = "stopped"
                break
            reports.append((load_agent(role).title, result.final_text))
        if reports and run.kind == "cycle" and status == "done":
            path = write_record(config, cadence, reports)
            run.emit({"type": "status", "role": "", "text": f"記録を書き出しました: {path.name}"})
        if notify.should_notify(settings, status):
            try:
                notify.send(settings["webhook_url"], notify.build_message(run.summary(), status, reports, usage))
                run.emit({"type": "status", "role": "", "text": "Webhook に知らせました"})
            except RuntimeError as exc:
                run.emit({"type": "status", "role": "", "text": f"Webhook に知らせられませんでした: {exc}"})
    except Exception as exc:  # どこで落ちてもランを「running」のまま残さない
        run.emit({"type": "error", "role": "", "text": f"実行中に落ちました: {type(exc).__name__}: {exc}"})
        status = "error"
    finally:
        run.finish(status)


def start_cycle(cadence: str, instruction: str | None = None) -> Run:
    config = load_config()
    roles = config.roles_for(cadence)
    if not roles:
        raise ValueError(f"cadence '{cadence}' が company.yaml にありません。")
    run = REGISTRY.create("cycle", cadence, instruction)
    threading.Thread(target=_execute, args=(run, roles, cadence, instruction), daemon=True).start()
    return run


def start_role(role: str, instruction: str | None = None) -> Run:
    load_agent(role)  # 存在確認
    run = REGISTRY.create("role", role, instruction)
    threading.Thread(target=_execute, args=(run, [role], "manual", instruction), daemon=True).start()
    return run
