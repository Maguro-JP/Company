"""コマンドライン。

    python -m churn_radar events.csv
    python -m churn_radar events.csv --today 2026-09-01 --window 7
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .detect import DROP_RATIO, WINDOW_DAYS, Signal, detect, load_events

# 全角の表示幅が揃うよう、ラベルは同じ文字数にする。
LABEL = {
    "stopped": "停止",
    "high": "急減",
    "watch": "減少",
}


def _format(signals: list[Signal], window: int) -> str:
    if not signals:
        return f"直近{window}日で、平常時から目立って減ったアカウントはありません。"

    lines = [f"予兆 {len(signals)} 件（平常時の{window}日間と比較）", ""]
    width = max(len(s.account) for s in signals)
    for s in signals:
        lines.append(
            f"  {s.account.ljust(width)}  {LABEL[s.severity]}  "
            f"稼働 {s.baseline_active_days}日 → {s.recent_active_days}日  "
            f"最終 {s.last_seen} ({s.days_since_last_seen}日前)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="churn_radar",
        description="アカウント自身の過去と比べて、解約の予兆を検出する",
    )
    parser.add_argument("events", type=Path, help="account_id,date の列を持つCSV")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=date.today(),
        help="基準日 (YYYY-MM-DD)。省略時は今日",
    )
    parser.add_argument(
        "--window", type=int, default=WINDOW_DAYS, help=f"比較する日数 (既定 {WINDOW_DAYS})"
    )
    parser.add_argument(
        "--drop",
        type=float,
        default=DROP_RATIO,
        help=f"予兆とみなす稼働日数の比率 (既定 {DROP_RATIO})",
    )
    args = parser.parse_args(argv)

    if args.window < 2:
        parser.error("--window は2以上にしてください")
    if not 0 < args.drop <= 1:
        parser.error("--drop は0より大きく1以下にしてください")
    if not args.events.exists():
        print(f"ファイルが見つかりません: {args.events}", file=sys.stderr)
        return 1

    try:
        active = load_events(args.events)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    signals = detect(active, args.today, window_days=args.window, drop_ratio=args.drop)
    print(_format(signals, args.window))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
