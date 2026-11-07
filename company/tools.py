"""エージェントに渡すツール。全てリポジトリ内に閉じる。

パスは ROOT 配下に制限する。コマンド実行は ROOT を作業ディレクトリに、タイムアウト付き。
Windows / Linux の両方で動くよう shell=True で実行する。
"""

from __future__ import annotations

import re
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .config import ROOT
from .providers import ToolSpec

MAX_OUTPUT_CHARS = 20_000
COMMAND_TIMEOUT_SEC = 300

# 憲章 4 節「テストが落ちた状態でコミットしない」を、お願いではなく道具の側で止める。
# コミットの直前にこのコマンドを走らせ、落ちていればコミット自体を実行しない。
# git の後ろに来てよいのは、オプション（-c key=val、-C path、-q など）だけ。`git log --grep commit` を巻き込まない
COMMIT_PATTERN = re.compile(r"\bgit(?:\s+-\S*(?:\s+[^-\s]\S*)?)*\s+commit\b")
# Python の道に空白が入る（C:\Program Files\Company\python）ので引用符で包む。包まずに落ちた
PYTHON = f'"{sys.executable}"'
TEST_COMMAND = f"cd product && {PYTHON} -m unittest discover -s tests"

ToolFn = Callable[[dict[str, Any]], str]


def _resolve(raw: str) -> Path:
    path = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        # ROOT も resolve する。Windows の短い名前（RUNNER~1）のまま比べると、同じ場所でも外と判定して書き込みを拒んだ
        path.relative_to(Path(ROOT).resolve())
    except ValueError:
        raise PermissionError(f"リポジトリの外は触れません: {raw}")
    return path


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...（{len(text) - MAX_OUTPUT_CHARS} 文字省略）"


def read_file(args: dict[str, Any]) -> str:
    path = _resolve(args["path"])
    if not path.exists():
        return f"ファイルが存在しません: {args['path']}"
    if path.is_dir():
        return f"ディレクトリです。list_files を使ってください: {args['path']}"
    return _clip(path.read_text(encoding="utf-8", errors="replace"))


def write_file(args: dict[str, Any]) -> str:
    path = _resolve(args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    content = args.get("content", "")
    path.write_text(content, encoding="utf-8", newline="\n")
    return f"書き込みました: {args['path']} ({len(content)} 文字)"


def list_files(args: dict[str, Any]) -> str:
    pattern = args.get("pattern") or "**/*"
    skip = {".git", "__pycache__", "node_modules", ".venv", "venv"}
    found = []
    for path in sorted(ROOT.glob(pattern)):
        if any(part in skip for part in path.parts):
            continue
        if path.is_file():
            found.append(path.relative_to(ROOT).as_posix())
        if len(found) >= 500:
            found.append("...（500件で打ち切り）")
            break
    return "\n".join(found) if found else "（該当なし）"


def _run(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        shell=True,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=COMMAND_TIMEOUT_SEC,
    )


def _tests_block_commit(command: str) -> str | None:
    """コミットなら先にテストを走らせ、落ちていれば断る文を返す。通れば None。"""
    if not COMMIT_PATTERN.search(command) or not TEST_COMMAND:
        return None
    try:
        proc = _run(TEST_COMMAND)
    except subprocess.TimeoutExpired:
        return f"コミットを止めました: テストが {COMMAND_TIMEOUT_SEC} 秒で終わりませんでした（{TEST_COMMAND}）"
    if proc.returncode == 0:
        return None
    tail = "\n".join((proc.stderr or proc.stdout or "").rstrip().splitlines()[-15:])
    return (
        "コミットを止めました: テストが落ちています（ガードレール「テストが落ちている状態でコミットしない」）。"
        f"先に直してください。\n実行: {TEST_COMMAND}\nexit code: {proc.returncode}\n{tail}"
    )


def _command_env() -> dict[str, str]:
    """配布版（同梱の Python）でも `python` が通るように、動かしている Python の場所を PATH の先頭に足す。"""
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


def run_command(args: dict[str, Any]) -> str:
    command = args["command"]
    refusal = _tests_block_commit(command)
    if refusal:
        return _clip(refusal)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(ROOT),
            env=_command_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return f"タイムアウト（{COMMAND_TIMEOUT_SEC}秒）: {command}"
    out = proc.stdout
    if proc.stderr:
        out += ("\n[stderr]\n" if out else "[stderr]\n") + proc.stderr
    out = out.rstrip() or "（出力なし）"
    return _clip(f"exit code: {proc.returncode}\n{out}")


READ_ONLY: dict[str, tuple[ToolSpec, ToolFn]] = {
    "read_file": (
        ToolSpec(
            name="read_file",
            description="リポジトリ内のファイルを読む。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "リポジトリルートからの相対パス"}},
                "required": ["path"],
            },
        ),
        read_file,
    ),
    "list_files": (
        ToolSpec(
            name="list_files",
            description="リポジトリ内のファイルを glob で列挙する。例: 'product/**/*.py'",
            parameters={
                "type": "object",
                "properties": {"pattern": {"type": "string", "description": "glob パターン。省略時は全て"}},
            },
        ),
        list_files,
    ),
    "run_command": (
        ToolSpec(
            name="run_command",
            description=(
                "シェルコマンドをリポジトリルートで実行し、終了コードと出力を返す。"
                f"テスト実行や git に使う。Python は `{Path(sys.executable).name}` で呼べる。"
            ),
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        ),
        run_command,
    ),
}

WRITE: dict[str, tuple[ToolSpec, ToolFn]] = {
    **READ_ONLY,
    "write_file": (
        ToolSpec(
            name="write_file",
            description="リポジトリ内のファイルを作成または上書きする。必要なディレクトリは自動で作る。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "リポジトリルートからの相対パス"},
                    "content": {"type": "string", "description": "ファイル全体の内容"},
                },
                "required": ["path", "content"],
            },
        ),
        write_file,
    ),
}


def toolset(can_write: bool) -> dict[str, tuple[ToolSpec, ToolFn]]:
    return WRITE if can_write else READ_ONLY
