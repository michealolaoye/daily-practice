import os
import random
import subprocess
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import cronexpr  # noqa: E402
from cronexpr import CronError, CronExpr, next_runs  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "cronexpr.py")


def dt(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M")


def brute(expr, start, count, limit_days=800):
    """Check every minute from the definition; slow, and only used as a reference."""
    out = []
    t = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = start + timedelta(days=limit_days)
    while t <= end and len(out) < count:
        cron_dow = (t.weekday() + 1) % 7
        dom = t.day in expr.doms
        dow = cron_dow in expr.dows
        day_ok = (dom or dow) if (expr.restricted_dom and expr.restricted_dow) else (dom and dow)
        if t.minute in expr.minutes and t.hour in expr.hours and t.month in expr.months and day_ok:
            out.append(t)
        t += timedelta(minutes=1)
    return out


class ParseTests(unittest.TestCase):
    def fields(self, text):
        e = CronExpr(text)
        return e.minutes, e.hours, e.doms, e.months, e.dows

    def test_star_fills_the_whole_range(self):
        m, h, d, mo, w = self.fields("* * * * *")
        self.assertEqual((len(m), len(h), len(d), len(mo), len(w)), (60, 24, 31, 12, 7))

    def test_lists_ranges_and_steps(self):
        m, h, *_ = self.fields("0,15,30-32,50/5 */6 * * *")
        self.assertEqual(sorted(m), [0, 15, 30, 31, 32, 50, 55])
        self.assertEqual(sorted(h), [0, 6, 12, 18])

    def test_range_with_step(self):
        m, *_ = self.fields("10-30/10 * * * *")
        self.assertEqual(sorted(m), [10, 20, 30])

    def test_names_are_case_insensitive(self):
        *_, mo, w = self.fields("0 0 * JAN,mar-May mon-Fri")
        self.assertEqual(sorted(mo), [1, 3, 4, 5])
        self.assertEqual(sorted(w), [1, 2, 3, 4, 5])

    def test_sunday_is_zero_or_seven(self):
        self.assertEqual(self.fields("0 0 * * 7")[4], frozenset({0}))
        self.assertEqual(self.fields("0 0 * * 0,7")[4], frozenset({0}))
        self.assertEqual(self.fields("0 0 * * 5-7")[4], frozenset({5, 6, 0}))

    def test_weekday_star_step_stops_at_saturday(self):
        # */2 over 0-7 would otherwise pick up 7 (Sunday) as well as 0.
        self.assertEqual(self.fields("0 0 * * */2")[4], frozenset({0, 2, 4, 6}))
        self.assertEqual(self.fields("0 0 * * 1/2")[4], frozenset({1, 3, 5}))

    def test_shortcuts(self):
        self.assertEqual(self.fields("@hourly"), self.fields("0 * * * *"))
        self.assertEqual(self.fields("@DAILY"), self.fields("@midnight"))
        self.assertEqual(self.fields("@weekly")[4], frozenset({0}))
        self.assertEqual(self.fields("@yearly"), self.fields("@annually"))

    def test_extra_whitespace_is_fine(self):
        self.assertEqual(self.fields("  5   *  * *\t* "), self.fields("5 * * * *"))

    def test_bad_expressions(self):
        bad = [
            "", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *",
            "* * 32 * *", "* * * 13 *", "* * * * 8", "5-1 * * * *", "*/0 * * * *",
            "*/x * * * *", "1,,2 * * * *", ", * * * *", "a * * * *", "* * * foo *",
            "@sometimes", "-1 * * * *", "１ * * * *", "1- * * * *",
        ]
        for text in bad:
            with self.assertRaises(CronError, msg=text):
                CronExpr(text)

    def test_error_names_the_field(self):
        with self.assertRaisesRegex(CronError, "hour: 24 is outside 0-23"):
            CronExpr("0 24 * * *")
        with self.assertRaisesRegex(CronError, "day of week"):
            CronExpr("0 0 * * funday")

    def test_wrong_type(self):
        with self.assertRaises(TypeError):
            CronExpr(None)


class NextTests(unittest.TestCase):
    def test_strictly_after_the_start(self):
        e = CronExpr("30 10 * * *")
        self.assertEqual(e.next_after(dt("2026-11-20 10:29")), dt("2026-11-20 10:30"))
        self.assertEqual(e.next_after(dt("2026-11-20 10:30")), dt("2026-11-21 10:30"))

    def test_seconds_in_the_start_are_ignored(self):
        e = CronExpr("* * * * *")
        start = datetime(2026, 11, 20, 10, 30, 45, 123)
        self.assertEqual(e.next_after(start), dt("2026-11-20 10:31"))

    def test_rolls_over_hour_day_month_year(self):
        self.assertEqual(CronExpr("0 * * * *").next_after(dt("2026-11-20 23:30")), dt("2026-11-21 00:00"))
        self.assertEqual(CronExpr("0 0 1 * *").next_after(dt("2026-11-20 10:00")), dt("2026-12-01 00:00"))
        self.assertEqual(CronExpr("0 0 1 1 *").next_after(dt("2026-11-20 10:00")), dt("2027-01-01 00:00"))
        self.assertEqual(CronExpr("59 23 31 12 *").next_after(dt("2026-12-31 23:59")), dt("2027-12-31 23:59"))

    def test_weekday_filter(self):
        runs = next_runs("0 9 * * mon-fri", dt("2026-11-20 10:00"), 4)  # a Friday
        self.assertEqual([r.strftime("%m-%d %a") for r in runs], ["11-23 Mon", "11-24 Tue", "11-25 Wed", "11-26 Thu"])

    def test_sunday_as_seven_matches_real_sundays(self):
        r = CronExpr("0 0 * * 7").next_after(dt("2026-11-20 00:00"))
        self.assertEqual(r.strftime("%a"), "Sun")
        self.assertEqual(r, dt("2026-11-22 00:00"))

    def test_day_of_month_or_day_of_week_when_both_restricted(self):
        runs = next_runs("0 0 13 * fri", dt("2026-11-01 00:00"), 6)
        got = [r.strftime("%m-%d") for r in runs]
        # Every Friday in November, plus the 13th (a Friday) once.
        self.assertEqual(got, ["11-06", "11-13", "11-20", "11-27", "12-04", "12-11"])
        runs = next_runs("0 0 14 * fri", dt("2026-11-01 00:00"), 3)
        self.assertEqual([r.strftime("%m-%d") for r in runs], ["11-06", "11-13", "11-14"])

    def test_dom_star_means_only_weekday_counts_and_vice_versa(self):
        only_dow = next_runs("0 0 * * mon", dt("2026-11-20 00:00"), 2)
        self.assertEqual({r.strftime("%a") for r in only_dow}, {"Mon"})
        only_dom = next_runs("0 0 15 * *", dt("2026-11-20 00:00"), 2)
        self.assertEqual([r.day for r in only_dom], [15, 15])

    def test_leap_day(self):
        runs = next_runs("0 0 29 2 *", dt("2026-11-20 00:00"), 2)
        self.assertEqual(runs, [dt("2028-02-29 00:00"), dt("2032-02-29 00:00")])

    def test_short_months_skip_day_31(self):
        runs = next_runs("0 12 31 * *", dt("2026-01-31 12:00"), 4)
        self.assertEqual([r.strftime("%m-%d") for r in runs], ["03-31", "05-31", "07-31", "08-31"])

    def test_impossible_date_returns_none(self):
        self.assertIsNone(CronExpr("0 0 30 2 *").next_after(dt("2026-11-20 00:00")))
        self.assertEqual(next_runs("0 0 31 4 *", dt("2026-11-20 00:00"), 3), [])

    def test_near_the_end_of_the_calendar_does_not_crash(self):
        self.assertIsNone(CronExpr("0 0 1 1 *").next_after(datetime(9999, 6, 1)))

    def test_matches_a_minute_by_minute_search(self):
        rng = random.Random(20261120)
        pieces = {
            "minute": ["*", "0", "*/15", "5,35", "10-20/5", "59", "7"],
            "hour": ["*", "0", "*/6", "9-17", "23", "3,15"],
            "dom": ["*", "1", "15", "29-31", "*/10", "13"],
            "month": ["*", "2", "1,7", "*/4", "dec", "feb-apr"],
            "dow": ["*", "0", "mon-fri", "7", "sat,sun", "2"],
        }
        for _ in range(60):
            text = " ".join(rng.choice(p) for p in pieces.values())
            expr = CronExpr(text)
            start = dt("2026-11-20 10:07") + timedelta(minutes=rng.randrange(0, 60 * 24 * 90))
            want = brute(expr, start, 3)
            got = expr.next_runs(start, 3)
            # The search is capped at 8 years and the reference at ~2, so compare
            # only as far as the reference looked.
            self.assertEqual(got[: len(want)], want, f"{text!r} from {start}")


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        r = subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True, text=True)
        return r.returncode, r.stdout.replace("\r\n", "\n"), r.stderr

    def test_prints_times(self):
        code, out, _ = self.run_cli("*/20 9-10 * * mon-fri", "--from", "2026-11-20 10:30", "-n", "4")
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(),
            ["2026-11-20 10:40 Fri", "2026-11-23 09:00 Mon", "2026-11-23 09:20 Mon", "2026-11-23 09:40 Mon"],
        )

    def test_date_only_start(self):
        code, out, _ = self.run_cli("@daily", "--from", "2026-11-20", "-n", "1")
        self.assertEqual((code, out), (0, "2026-11-21 00:00 Sat\n"))

    def test_bad_expression_exits_1(self):
        code, out, err = self.run_cli("61 * * * *")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("minute: 61 is outside 0-59", err)

    def test_never_runs_exits_1(self):
        code, out, err = self.run_cli("0 0 30 2 *", "--from", "2026-11-20")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("never runs", err)

    def test_usage_errors_exit_2(self):
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli("* * * * *", "-n", "0")[0], 2)
        self.assertEqual(self.run_cli("* * * * *", "--from", "tomorrow")[0], 2)


if __name__ == "__main__":
    unittest.main()
