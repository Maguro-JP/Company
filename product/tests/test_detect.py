"""検出ロジックの検証。誤検知と見落としの両方を押さえる。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from churn_radar.cli import main  # noqa: E402
from churn_radar.detect import detect, load_events  # noqa: E402

TODAY = date(2026, 9, 1)


def days(start_offset: int, count: int, step: int = 1) -> set[date]:
    """TODAY から start_offset 日前を起点に、count 日分の稼働日を作る。"""
    return {TODAY - timedelta(days=start_offset - i * step) for i in range(count)}


class DetectTest(unittest.TestCase):
    def test_account_that_stopped_completely_is_flagged(self):
        # 平常時は10日稼働、直近14日はゼロ
        active = {"a": days(27, 10)}
        (signal,) = detect(active, TODAY)
        self.assertEqual(signal.severity, "stopped")
        self.assertEqual(signal.recent_active_days, 0)
        self.assertEqual(signal.drop, 1.0)

    def test_steady_account_is_not_flagged(self):
        # 前後どちらも同じペース
        active = {"a": days(27, 14, step=2)}
        self.assertEqual(detect(active, TODAY), [])

    def test_increasing_account_is_not_flagged(self):
        active = {"a": days(27, 3) | days(13, 12)}
        self.assertEqual(detect(active, TODAY), [])

    def test_halved_usage_is_flagged(self):
        # 平常10日 → 直近3日。7割減なので、危険域(75%)手前の「要観察」
        active = {"a": days(27, 10) | days(13, 3)}
        (signal,) = detect(active, TODAY)
        self.assertEqual(signal.severity, "watch")
        self.assertEqual(signal.recent_active_days, 3)

    def test_severe_drop_is_ranked_above_watch(self):
        # 平常10日 → 直近1日。9割減
        active = {"a": days(27, 10) | days(13, 1)}
        (signal,) = detect(active, TODAY)
        self.assertEqual(signal.severity, "high")

    def test_small_dip_is_not_flagged(self):
        # 平常10日 → 直近7日。50%を下回っていない
        active = {"a": days(27, 10) | days(13, 7)}
        self.assertEqual(detect(active, TODAY), [])

    def test_light_user_is_ignored_for_lack_of_baseline(self):
        """元々ほとんど使っていないアカウントは、比較できないので判定しない。"""
        active = {"a": days(27, 2)}
        self.assertEqual(detect(active, TODAY), [])

    def test_brand_new_account_is_ignored(self):
        """今週使い始めたばかりのアカウントを、減少と誤認しない。"""
        active = {"a": days(5, 5)}
        self.assertEqual(detect(active, TODAY), [])

    def test_stopped_accounts_come_first(self):
        active = {
            "still-here": days(27, 10) | days(13, 3),
            "gone": days(27, 10),
        }
        order = [s.account for s in detect(active, TODAY)]
        self.assertEqual(order, ["gone", "still-here"])

    def test_same_day_repeats_count_once(self):
        """1日に何回使っても1日。イベント数の多寡に判定が引きずられない。"""
        heavy = {"a": days(27, 10)}
        signals = detect(heavy, TODAY)
        self.assertEqual(signals[0].baseline_active_days, 10)

    def test_window_is_configurable(self):
        active = {"a": days(13, 5)}
        # 14日窓では平常時が空なので判定対象外
        self.assertEqual(detect(active, TODAY, window_days=14), [])
        # 7日窓なら平常時に5日ぶんが入り、直近ゼロとして検出される
        (signal,) = detect(active, TODAY, window_days=7)
        self.assertEqual(signal.severity, "stopped")


class LoadEventsTest(unittest.TestCase):
    def _write(self, text: str) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "events.csv"
        tmp.write_text(text, encoding="utf-8")
        return tmp

    def test_reads_account_and_date(self):
        path = self._write("account_id,date\nacme,2026-08-01\nacme,2026-08-02\n")
        self.assertEqual(load_events(path), {"acme": {date(2026, 8, 1), date(2026, 8, 2)}})

    def test_accepts_timestamps(self):
        path = self._write("account_id,date\nacme,2026-08-01T09:30:00Z\n")
        self.assertEqual(load_events(path), {"acme": {date(2026, 8, 1)}})

    def test_extra_columns_are_ignored(self):
        path = self._write("account_id,date,event\nacme,2026-08-01,login\n")
        self.assertEqual(load_events(path), {"acme": {date(2026, 8, 1)}})

    def test_blank_rows_are_skipped(self):
        path = self._write("account_id,date\nacme,2026-08-01\n,\nacme,\n")
        self.assertEqual(load_events(path), {"acme": {date(2026, 8, 1)}})

    def test_missing_column_names_the_problem(self):
        path = self._write("user,when\nacme,2026-08-01\n")
        with self.assertRaises(ValueError) as ctx:
            load_events(path)
        self.assertIn("account_id", str(ctx.exception))

    def test_bad_date_names_the_row(self):
        path = self._write("account_id,date\nacme,2026-08-01\nacme,yesterday\n")
        with self.assertRaises(ValueError) as ctx:
            load_events(path)
        self.assertIn(":3", str(ctx.exception))


class CliTest(unittest.TestCase):
    def test_missing_file_exits_nonzero(self):
        self.assertEqual(main(["/nonexistent/events.csv"]), 1)

    def test_runs_on_a_real_file(self):
        tmp = Path(tempfile.mkdtemp()) / "events.csv"
        rows = ["account_id,date"]
        for d in sorted(days(27, 10)):
            rows.append(f"acme,{d.isoformat()}")
        tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
        self.assertEqual(main([str(tmp), "--today", "2026-09-01"]), 0)

    def test_rejects_invalid_window(self):
        with self.assertRaises(SystemExit):
            main(["x.csv", "--window", "1"])


if __name__ == "__main__":
    unittest.main()
