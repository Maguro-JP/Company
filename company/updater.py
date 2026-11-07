"""アプリ本体の更新を確かめて、新しい Setup.exe を取る。CharacterDesigner の chatdraw/updater.py と同じ形。

公開されている最新の版（GitHub の releases/latest）と手元の VERSION を比べる。新しければ Setup.exe を更新の置き場
（作業場の updates\\）に取って、入れるかどうかはユーザーが決める（勝手に入れない）。
確かめられないとき（回線が無い、公開されていない）は、自分で押したときだけ理由を出し、起動時の確認は黙る。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import paths

REPO = "maguro-jp/company"
URL = os.environ.get("COMPANY_UPDATE_URL", f"https://api.github.com/repos/{REPO}/releases/latest")


def current() -> str:
    try:
        return (paths.APP_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


def _tuple(v: str) -> tuple:
    out = []
    for part in v.lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def newer(latest: str, mine: str) -> bool:
    return _tuple(latest) > _tuple(mine)


def updates_dir() -> Path:
    return paths.workspace_root() / "updates"


def check(url: str = URL, timeout: float = 8.0, quiet: bool = True) -> dict[str, Any] | None:
    """新しい版があれば {version, setup, page, notes} を返す。無い・確かめられないなら None。
    quiet=False なら、確かめられないときは例外のまま返す（押したのに「最新です」と出さない）。"""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Company"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        if not quiet:
            raise
        return None
    version = str(data.get("tag_name") or data.get("version") or "")
    if not version or not newer(version, current()):
        return None
    setup = next((a.get("browser_download_url") for a in data.get("assets", [])
                  if str(a.get("name", "")).lower().endswith(".exe")), None)
    return {"version": version.lstrip("vV"), "setup": setup, "page": data.get("html_url", ""),
            "notes": (data.get("body") or "").strip()[:400]}


def download(url: str, on_progress: Callable[[int, int], None] | None = None) -> Path:
    """Setup.exe を更新の置き場に取る。途中で切れても壊れたファイルを残さないよう、別名で取ってから名前を替える。"""
    dest = updates_dir() / url.rsplit("/", 1)[-1]
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Company"})
    with urllib.request.urlopen(req, timeout=60) as r, part.open("wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)
    part.replace(dest)
    return dest


def launch(setup: Path) -> None:
    """取った Setup.exe を起こす。入れるのは Setup.exe の案内に沿ってユーザーが進める。Windows でだけ動く。"""
    if sys.platform != "win32":
        raise RuntimeError("Setup.exe は Windows でだけ実行できます")
    subprocess.Popen([str(setup)], close_fds=True)
