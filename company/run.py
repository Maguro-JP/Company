"""CLI エントリポイント。UIを使わずに回すとき用。

    python -m company.run --check
    python -m company.run --cadence build
    python -m company.run --role builder --instruction "JSONも読めるように"
"""

from __future__ import annotations

import argparse
import sys

from . import runner
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIが経営する会社を1サイクル動かす")
    parser.add_argument("--cadence", help="company.yaml の cadence キー (build / review)")
    parser.add_argument("--role", help="1つの役職だけ動かす")
    parser.add_argument("--instruction", help="オーナーからの指示（任意）")
    parser.add_argument("--check", action="store_true", help="設定の確認だけして終了する")
    args = parser.parse_args(argv)

    config = load_config()
    missing = config.unset_fields()
    settings = runner.load_settings()

    if args.check:
        if missing:
            print("company.yaml に未設定の項目があります:")
            for field in missing:
                print(f"  - {field}")
        else:
            print("company.yaml の必須項目は埋まっています。")
        print(f"プロバイダ: {settings['provider']}  モデル: {settings['model'] or '（未選択。UIかdata/settings.jsonで設定）'}")
        return 1 if missing or not settings["model"] else 0

    if missing:
        print("company.yaml に未設定の項目があります。先に埋めてください:", *missing, sep="\n  - ")
        return 1
    if not settings["model"]:
        print("モデルが未選択です。`python app.py` のUIで選ぶか、data/settings.json に書いてください。")
        return 1

    if bool(args.cadence) == bool(args.role):
        parser.error("--cadence か --role のどちらか一方を指定してください")

    try:
        run = runner.start_cycle(args.cadence, args.instruction) if args.cadence else runner.start_role(args.role, args.instruction)
    except (ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    index = 0
    while True:
        for event in run.wait_for(index, timeout=5):
            index += 1
            _print(event)
        if run.status != "running" and index >= len(run.events):
            break
    print(f"\n終了: {run.status}")
    return 0 if run.status == "done" else 1


def _print(event: dict) -> None:
    role = event.get("role") or "-"
    kind = event["type"]
    if kind == "text":
        print(f"\n[{role}]\n{event['text']}")
    elif kind == "tool_call":
        print(f"  → {event['name']} {event['args']}")
    elif kind == "tool_result":
        first = event["text"].splitlines()[0] if event["text"] else ""
        print(f"  ← {first}")
    elif kind in ("status", "error", "role_start"):
        print(f"[{role}] {event.get('text') or event.get('title', '')}")


if __name__ == "__main__":
    raise SystemExit(main())
