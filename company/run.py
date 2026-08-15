"""CLI エントリポイント。

    python -m company.run --cadence weekly
    python -m company.run --cadence weekly --dry-run
    python -m company.run --check
"""

from __future__ import annotations

import argparse

from . import board
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIが経営する会社を1サイクル動かす")
    parser.add_argument(
        "--cadence",
        default="weekly",
        help="company.yaml の cadence キー (daily / weekly / monthly)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="APIを呼ばず、実際に渡されるコンテキストだけを表示する",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="company.yaml の未設定項目を確認して終了する",
    )
    args = parser.parse_args(argv)

    config = load_config()

    missing = config.unset_fields()
    if args.check:
        if missing:
            print("未設定の項目があります:")
            for field in missing:
                print(f"  - {field}")
            return 1
        print("company.yaml の必須項目は埋まっています。")
        return 0

    if missing and not args.dry_run:
        print("company.yaml がまだ TODO のままです。先に埋めてください:")
        for field in missing:
            print(f"  - {field}")
        print("\n（コンテキストの確認だけなら --dry-run が使えます）")
        return 1

    reports = board.run_cycle(config, args.cadence, dry_run=args.dry_run)
    if args.dry_run:
        return 0

    path = board.write_record(config, args.cadence, reports)
    print(f"\n記録を書き出しました: {path}")
    print("内容を確認し、オーナー承認欄を埋めてからコミットしてください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
