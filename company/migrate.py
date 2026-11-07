"""配布版の作業場を用意する。最初の起動と更新のたびに app.py が呼ぶ。Setup.exe も前の版からの移行で呼ぶ。

    python -m company.migrate                 Program Files の中身から作業場（ProgramData\\Company）を整える
    python -m company.migrate <前の版の場所>  v0.1.x（%LOCALAPPDATA%\\Programs\\Company）の作業場と記録を移す

決まり:
- 作業場に無い物だけを足す。ある物は書き換えない（AI が直したプロダクトと記録を、更新で消さない）
- 前の版の data/models の .gguf は共有の置き場（C:\\LLM\\gguf）へ移す。同じ名前があれば古い方を消す
- 失敗しても例外で止めない。記録は作業場の migrate.log に残す
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import paths


def _log(root: Path, text: str) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        with (root / "migrate.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {text}\n")
    except OSError:
        pass


def copy_missing(src: Path, dst: Path) -> int:
    """src にあって dst に無いファイルだけを写す。写した数を返す。"""
    count = 0
    if src.is_file():
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
        return count
    for item in src.rglob("*"):
        if item.is_dir() or "__pycache__" in item.parts or ".git" in item.parts:
            continue
        target = dst / item.relative_to(src)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            count += 1
    return count


def ensure_workspace(app_root: Path, workspace: Path) -> int:
    """作業場にプログラムの同梱物（company.yaml、agents、product、docs）を足す。既にある物は触らない。"""
    if workspace.resolve() == app_root.resolve():
        return 0
    added = 0
    for name in paths.WORKSPACE_ITEMS:
        src = app_root / name
        if src.exists():
            added += copy_missing(src, workspace / name)
    (workspace / "data" / "decisions").mkdir(parents=True, exist_ok=True)
    ignore = workspace / ".gitignore"
    if not ignore.exists():
        ignore.write_text("data/settings.json\ndata/models/\n__pycache__/\n*.pyc\nmigrate.log\n", encoding="utf-8")
    if added:
        _log(workspace, f"ensure_workspace: {added} files added from {app_root}")
    return added


def move_models(old_models: Path, models_dir: Path) -> int:
    """前の置き場の .gguf を共有の置き場へ移す。"""
    moved = 0
    if not old_models.is_dir():
        return 0
    paths.ensure_models_dir(models_dir)
    for gguf in old_models.glob("*.gguf"):
        target = models_dir / gguf.name
        if target.exists() and target.stat().st_size >= gguf.stat().st_size:
            gguf.unlink()
        else:
            shutil.move(str(gguf), str(target))
        moved += 1
    return moved


def migrate_old(old_root: Path, workspace: Path, models_dir: Path) -> dict[str, int]:
    """v0.1.x の作業場と記録を新しい作業場へ移す。無い物だけ足す。"""
    result = {"files": 0, "models": 0}
    if not old_root.is_dir():
        return result
    for name in (*paths.WORKSPACE_ITEMS, "data"):
        src = old_root / name
        if name == "data" and src.is_dir():
            for sub in src.iterdir():
                if sub.name == "models":
                    continue
                result["files"] += copy_missing(sub, workspace / "data" / sub.name)
            continue
        if src.exists():
            result["files"] += copy_missing(src, workspace / name)
    result["models"] = move_models(old_root / "data" / "models", models_dir)
    _log(workspace, f"migrate_old from {old_root}: {result}")
    return result


def main(argv: list[str]) -> int:
    workspace = paths.workspace_root()
    models = paths.models_dir()
    try:
        ensure_workspace(paths.APP_ROOT, workspace)
        if argv:
            migrate_old(Path(argv[0]), workspace, models)
    except Exception as exc:  # 移行で止まるより、動く状態で起動させる
        _log(workspace, f"failed: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
