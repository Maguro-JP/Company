"""解約の予兆を、アカウント自身の過去と比べて検出する。

他社比較や絶対値のしきい値は使わない。週1回しか使わないのが平常なユーザーと、
毎日使うのが平常なユーザーでは、「減った」の意味が違うため。
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# 直近この日数を「現在」、その手前の同じ長さを「平常時」とみなす。
WINDOW_DAYS = 14

# 平常時にこの日数未満しか使っていないアカウントは、比較の土台が無いので判定しない。
MIN_BASELINE_ACTIVE_DAYS = 3

# 稼働日数がこの割合を下回ったら予兆とみなす。
DROP_RATIO = 0.5


@dataclass(frozen=True)
class Signal:
    account: str
    baseline_active_days: int
    recent_active_days: int
    last_seen: date
    days_since_last_seen: int

    @property
    def drop(self) -> float:
        """平常時から何割減ったか。1.0 = 完全に止まった。"""
        if self.baseline_active_days == 0:
            return 0.0
        return 1 - (self.recent_active_days / self.baseline_active_days)

    @property
    def severity(self) -> str:
        if self.recent_active_days == 0:
            return "stopped"   # 完全に止まっている
        if self.drop >= 0.75:
            return "high"
        return "watch"


def load_events(path: Path) -> dict[str, set[date]]:
    """CSV (account_id,date) を、アカウントごとの稼働日の集合にする。

    同じ日に何回使っても1日と数える。イベント数は実装やプランで簡単に変わるが、
    「その日に触ったかどうか」は事業をまたいで意味が安定するため。
    """
    active: dict[str, set[date]] = defaultdict(set)
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"account_id", "date"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"CSVに必要な列がありません: {sorted(missing)} / "
                f"実際の列: {reader.fieldnames}"
            )
        for row_number, row in enumerate(reader, start=2):
            account = (row.get("account_id") or "").strip()
            raw_date = (row.get("date") or "").strip()
            if not account or not raw_date:
                continue
            try:
                parsed = date.fromisoformat(raw_date[:10])
            except ValueError as exc:
                raise ValueError(f"{path}:{row_number} 日付を解釈できません: {raw_date!r}") from exc
            active[account].add(parsed)
    return dict(active)


def detect(
    active: dict[str, set[date]],
    today: date,
    window_days: int = WINDOW_DAYS,
    drop_ratio: float = DROP_RATIO,
    min_baseline: int = MIN_BASELINE_ACTIVE_DAYS,
) -> list[Signal]:
    """予兆のあるアカウントを、深刻な順に返す。"""
    recent_start = today - timedelta(days=window_days - 1)
    baseline_start = recent_start - timedelta(days=window_days)

    signals: list[Signal] = []
    for account, days in active.items():
        if not days:
            continue
        recent = {d for d in days if recent_start <= d <= today}
        baseline = {d for d in days if baseline_start <= d < recent_start}

        if len(baseline) < min_baseline:
            continue
        if len(recent) >= len(baseline) * drop_ratio:
            continue

        last_seen = max(days)
        signals.append(
            Signal(
                account=account,
                baseline_active_days=len(baseline),
                recent_active_days=len(recent),
                last_seen=last_seen,
                days_since_last_seen=(today - last_seen).days,
            )
        )

    # 完全に止まったものを先頭に、次に減少幅の大きい順。
    return sorted(signals, key=lambda s: (s.recent_active_days > 0, -s.drop, s.account))
