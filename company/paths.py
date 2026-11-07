"""置き場所を 1 か所で決める。自作のアプリ（AdaptiveAIStudio、CharacterDesigner）と同じ並びにそろえる。

Windows の配布版:
    C:\\Program Files\\Company\\     プログラム（company、ui、python、app.py）。入れ直すたびに丸ごと置き換える。書き換えない
    C:\\ProgramData\\Company\\       作業場（company.yaml、agents、product、docs、data）。AI が書き換え、更新しても残る
    C:\\LLM\\gguf\\                  言葉の模型。自作のアプリどうしで共有する（.claude/skills/shared-models）

開発中（リポジトリから起動）: 作業場はリポジトリそのもの、模型は data/models。

データを ProgramData に置くのは、利用者名が全角だと AppData の道で llama.cpp が模型を開けないため。
ProgramData と C:\\LLM は必ず ASCII の道になる。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
WINDOWS = sys.platform == "win32"
APP = "Company"

# 作業場に置く物（配布版の最初の起動で Program Files からここへ写す。更新では無い物だけ足す）
WORKSPACE_ITEMS = ("company.yaml", "agents", "product", "docs", "README.md", "CLAUDE.md", ".claude")


def is_installed(env: dict[str, str] | None = None, app_root: Path | None = None) -> bool:
    """配布版（Setup.exe で入れた物）か。同梱の Python があり、git のリポジトリではない物を配布版とみなす。"""
    root = app_root or APP_ROOT
    return (root / "python" / "python.exe").exists() and not (root / ".git").exists()


def workspace_root(env: dict[str, str] | None = None, app_root: Path | None = None) -> Path:
    """作業場。COMPANY_HOME > 配布版なら ProgramData\\Company > リポジトリそのもの。"""
    env = os.environ if env is None else env
    if env.get("COMPANY_HOME"):
        return Path(env["COMPANY_HOME"])
    if is_installed(env, app_root):
        return Path(env.get("ProgramData") or "C:\\ProgramData") / APP
    return app_root or APP_ROOT


def models_dir(env: dict[str, str] | None = None, app_root: Path | None = None) -> Path:
    """言葉の模型の置き場。LLM_HOME > Windows の C:\\LLM > 開発中は data/models。"""
    env = os.environ if env is None else env
    if env.get("LLM_HOME"):
        return Path(env["LLM_HOME"]) / "gguf"
    if WINDOWS or is_installed(env, app_root):
        drive = (env.get("SystemDrive") or "C:") + "\\"
        return Path(drive) / "LLM" / "gguf"
    return workspace_root(env, app_root) / "data" / "models"


LLM_README = """この置き場には、自作のアプリが共有して使う AI の模型が入っています。

gguf\\         言葉の模型（llama.cpp で読む .gguf）
huggingface\\  絵の模型など、Hugging Face の形のままの模型

複数のアプリが同じ模型を読みます。消すと、使っているアプリで取り直しになります。
"""


def ensure_models_dir(path: Path) -> Path:
    """模型の置き場を作る。共有の置き場なら README を置く（既にあれば書き換えない）。"""
    path.mkdir(parents=True, exist_ok=True)
    if path.name == "gguf":
        readme = path.parent / "README.txt"
        if not readme.exists():
            try:
                readme.write_text(LLM_README, encoding="utf-8")
            except OSError:
                pass
    return path
