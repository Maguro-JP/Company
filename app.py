"""アプリの起動。

    python app.py            アプリウィンドウで開く（Windows: Edge WebView2）
    python app.py --browser  既定のブラウザで開く
    python app.py --port 8765
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

# Windows のコンソールは cp1252 のことがあり、日本語を print すると UnicodeEncodeError で起動ごと落ちる
# （Actions の Windows ランナーで実際に落ちた）。pythonw では stdout が無いので、その場合は触らない
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


def _free_port(preferred: int) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def _wait_ready(url: str, timeout: float = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def _ensure_git_repo() -> None:
    """配布版は git のリポジトリとして入らない。判断と実行の一次記録はコミット履歴（憲章 3 節）なので、
    git があれば最初の起動でリポジトリにする。git が無ければそのまま動き、コミットだけが失敗として報告される。"""
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parent
    if (root / ".git").exists() or not shutil.which("git"):
        return
    try:
        # 同梱の Python（150 MB）と記録は履歴に入れない。入れると最初のコミットに 1 分以上かかり、起動が遅れる
        ignore = root / ".gitignore"
        if not ignore.exists():
            ignore.write_text("python/\ndata/\n__pycache__/\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=30)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, timeout=120)
        subprocess.run(["git", "-c", "user.name=Company", "-c", "user.email=company@localhost",
                        "commit", "-q", "-m", "配布版の初期状態"], cwd=root, check=True, timeout=120)
    except (subprocess.SubprocessError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIが経営する会社を起動する")
    parser.add_argument("--browser", action="store_true", help="アプリウィンドウではなくブラウザで開く")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="サーバーだけ起動する")
    args = parser.parse_args(argv)

    _ensure_git_repo()
    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}"

    server = uvicorn.Server(uvicorn.Config("company.server:app", host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    if not _wait_ready(f"{url}/api/state"):
        print("サーバーが起動しませんでした", file=sys.stderr)
        return 1
    print(f"起動: {url}")

    if args.no_open:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0

    if not args.browser:
        try:
            import webview  # noqa: PLC0415
        except ImportError:
            print("pywebview が無いのでブラウザで開きます（pip install pywebview で窓になります）")
        else:
            webview.create_window("Company", url, width=1380, height=860, min_size=(960, 600))
            icon = str(Path(__file__).resolve().parent / "ui" / "icon" / "app-256.png")
            try:
                webview.start(icon=icon)
            except TypeError:  # icon を受けない古い pywebview
                webview.start()
            return 0

    webbrowser.open(url)
    print("終了するには Ctrl+C")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
