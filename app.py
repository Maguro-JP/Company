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

import uvicorn


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIが経営する会社を起動する")
    parser.add_argument("--browser", action="store_true", help="アプリウィンドウではなくブラウザで開く")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="サーバーだけ起動する")
    args = parser.parse_args(argv)

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
