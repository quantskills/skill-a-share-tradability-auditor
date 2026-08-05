"""Repository-local suite for the A-share tradability auditor.

Run from the repository root:
    python -B -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import pandas as pd  # noqa: E402

import audit_trades as auditor  # noqa: E402
import build_tradability_panel as builder  # noqa: E402
import validate_audit as contract  # noqa: E402


def bar(date, symbol, o, h, low, c, pre, vol, amount=1e9, is_st=0, list_date=""):
    return {
        "date": date, "symbol": symbol, "open": o, "high": h, "low": low,
        "close": c, "pre_close": pre, "volume": vol, "amount": amount,
        "is_st": is_st, "list_date": list_date,
    }


def panel_from(rows, **kwargs):
    return builder.build_panel(builder.load_bars_frame(pd.DataFrame(rows)), **kwargs)


def trades_from(rows):
    return auditor.load_trades_frame(pd.DataFrame(rows))


class BoardAndLimits(unittest.TestCase):
    def test_board_inference_covers_every_venue(self):
        cases = {
            "600519": "sh_main", "601318.SH": "sh_main", "603259": "sh_main",
            "000001": "sz_main", "002230": "sz_main", "001979": "sz_main",
            "300750": "chinext", "301999": "chinext",
            "688981": "star", "689009": "star",
            "830799": "bse", "920001": "bse", "430047": "bse",
        }
        for symbol, expected in cases.items():
            self.assertEqual(builder.infer_board(symbol), expected, symbol)

    def test_unknown_symbols_return_none_rather_than_a_guess(self):
        for symbol in ("AAPL", "12345", "", "1234567", "HK0700"):
            self.assertIsNone(builder.infer_board(symbol))

    def test_limit_price_uses_round_half_up_not_bankers_rounding(self):
        # 8.10 * 1.05 = 8.505 -> 8.51 at the exchange; Python's round() gives 8.50.
        self.assertEqual(builder.limit_price(8.10, 0.05, "up"), 8.51)
        self.assertEqual(builder.limit_price(10.0, 0.10, "up"), 11.0)
        self.assertEqual(builder.limit_price(10.0, 0.10, "down"), 9.0)
        self.assertEqual(builder.limit_price(50.0, 0.20, "down"), 40.0)

    def test_limit_price_rejects_unusable_inputs(self):
        self.assertIsNone(builder.limit_price(None, 0.1, "up"))
        self.assertIsNone(builder.limit_price(10.0, None, "up"))
        self.assertIsNone(builder.limit_price(-5.0, 0.1, "up"))

    def test_st_halved_the_band_on_main_boards_only_before_the_2026_change(self):
        rows = [
            bar("20240301", "600123", 8.0, 8.1, 7.9, 8.0, 8.0, 1000, is_st=1),
            bar("20240301", "300123", 8.0, 8.1, 7.9, 8.0, 8.0, 1000, is_st=1),
            bar("20240301", "688123", 8.0, 8.1, 7.9, 8.0, 8.0, 1000, is_st=1),
            bar("20240301", "830123", 8.0, 8.1, 7.9, 8.0, 8.0, 1000, is_st=1),
        ]
        panel = panel_from(rows).set_index("symbol")
        self.assertAlmostEqual(panel.loc["600123", "limit_pct"], 0.05)
        self.assertAlmostEqual(panel.loc["300123", "limit_pct"], 0.20)
        self.assertAlmostEqual(panel.loc["688123", "limit_pct"], 0.20)
        self.assertAlmostEqual(panel.loc["830123", "limit_pct"], 0.30)

    def test_main_board_st_band_widens_to_10pct_on_20260706(self):
        # 沪深交易所《交易规则》(2026-04-24 发布) 自 2026-07-06 起将主板风险警示股
        # 涨跌幅由 5% 调整为 10%。规则按 bar 日期解析，不能全样本一刀切。
        for date, expected in (("20260703", 0.05), ("20260706", 0.10),
                               ("20260707", 0.10)):
            rows = [bar(date, "600123", 8.0, 8.1, 7.9, 8.0, 8.0, 1000, is_st=1)]
            self.assertAlmostEqual(panel_from(rows).iloc[0]["limit_pct"], expected,
                                   msg="ST band wrong on %s" % date)

    def test_the_2026_change_does_not_touch_non_main_boards_or_normal_stocks(self):
        for symbol, is_st, expected in (("300123", 1, 0.20), ("688123", 1, 0.20),
                                        ("830123", 1, 0.30), ("600519", 0, 0.10)):
            rows = [bar("20260707", symbol, 8.0, 8.1, 7.9, 8.0, 8.0, 1000, is_st=is_st)]
            self.assertAlmostEqual(panel_from(rows).iloc[0]["limit_pct"], expected,
                                   msg="%s regressed after the 2026 change" % symbol)

    def test_st_limit_resolution_is_a_pure_function_of_board_flag_and_date(self):
        self.assertAlmostEqual(builder.limit_pct_for("sh_main", True, "20250101"), 0.05)
        self.assertAlmostEqual(builder.limit_pct_for("sh_main", True, "20270101"), 0.10)
        self.assertAlmostEqual(builder.limit_pct_for("sz_main", False, "20250101"), 0.10)
        self.assertIsNone(builder.limit_pct_for(None, True, "20250101"))

    def test_a_sample_spanning_the_rule_change_gets_both_bands(self):
        # The same ST name on both sides of the cutover must not share one band.
        rows = [
            bar("20260703", "600123", 8.0, 8.1, 7.9, 8.0, 8.0, 1000, is_st=1),
            bar("20260706", "600123", 8.0, 8.1, 7.9, 8.0, 8.0, 1000, is_st=1),
        ]
        panel = panel_from(rows).set_index("date")
        self.assertAlmostEqual(panel.loc["20260703", "limit_up"], 8.40)  # 8.0 * 1.05
        self.assertAlmostEqual(panel.loc["20260706", "limit_up"], 8.80)  # 8.0 * 1.10


class StateClassification(unittest.TestCase):
    def test_locked_limit_up_blocks_buying_but_not_selling(self):
        rows = [bar("20240301", "600519", 110.0, 110.0, 110.0, 110.0, 100.0, 900)]
        row = panel_from(rows).iloc[0]
        self.assertEqual(row["state"], "limit_up_locked")
        self.assertEqual(row["buy_capacity"], 0.0)
        self.assertEqual(row["sell_capacity"], 1.0)

    def test_limit_up_that_opened_intraday_gives_a_partial_fill(self):
        rows = [bar("20240301", "600519", 108.0, 110.0, 104.0, 110.0, 100.0, 50000)]
        row = panel_from(rows, limit_open_fill=0.25).iloc[0]
        self.assertEqual(row["state"], "limit_up_open")
        self.assertAlmostEqual(row["buy_capacity"], 0.25)

    def test_locked_limit_down_blocks_selling_but_not_buying(self):
        rows = [bar("20240301", "688981", 40.0, 40.0, 40.0, 40.0, 50.0, 5000)]
        row = panel_from(rows).iloc[0]
        self.assertEqual(row["state"], "limit_down_locked")
        self.assertEqual(row["sell_capacity"], 0.0)
        self.assertEqual(row["buy_capacity"], 1.0)

    def test_zero_volume_is_a_suspension(self):
        rows = [bar("20240301", "000001", 0.0, 0.0, 0.0, 10.0, 10.0, 0, amount=0.0)]
        row = panel_from(rows).iloc[0]
        self.assertEqual(row["state"], "halted")
        self.assertEqual(row["buy_capacity"], 0.0)
        self.assertEqual(row["sell_capacity"], 0.0)

    def test_a_bar_missing_while_the_universe_trades_is_a_suspension(self):
        rows = [
            bar("20240301", "600519", 99.0, 100.0, 98.0, 100.0, 100.0, 1000),
            bar("20240302", "600519", 99.0, 100.0, 98.0, 100.0, 100.0, 1000),
            bar("20240301", "000001", 9.9, 10.0, 9.8, 10.0, 10.0, 1000),
            # 000001 has no 20240302 bar although 600519 traded that day.
            bar("20240303", "000001", 9.9, 10.0, 9.8, 10.0, 10.0, 1000),
            bar("20240303", "600519", 99.0, 100.0, 98.0, 100.0, 100.0, 1000),
        ]
        panel = panel_from(rows)
        gap = panel[(panel["symbol"] == "000001") & (panel["date"] == "20240302")]
        self.assertEqual(len(gap), 1)
        self.assertEqual(gap.iloc[0]["state"], "halted")
        self.assertFalse(bool(gap.iloc[0]["bar_present"]))

    def test_dates_outside_a_symbols_own_lifespan_are_not_fabricated(self):
        rows = [
            bar("20240301", "600519", 99.0, 100.0, 98.0, 100.0, 100.0, 1000),
            bar("20240302", "600519", 99.0, 100.0, 98.0, 100.0, 100.0, 1000),
            bar("20240302", "301999", 30.0, 31.0, 29.0, 30.0, 30.0, 1000),
        ]
        panel = panel_from(rows)
        # 301999 was not listed on 20240301 -> no row, not a fake suspension.
        self.assertTrue(panel[(panel["symbol"] == "301999") &
                              (panel["date"] == "20240301")].empty)


class NewListingWindow(unittest.TestCase):
    def test_listing_day_pop_is_not_reported_as_a_limit_up(self):
        rows = [bar("20240305", "301999", 30.0, 60.0, 29.0, 55.0, 20.0, 200000,
                    list_date="20240305")]
        row = panel_from(rows).iloc[0]
        self.assertEqual(row["state"], "normal")
        self.assertTrue(bool(row["new_listing"]))
        self.assertFalse(bool(row["limit_reliable"]))
        self.assertTrue(pd.isna(row["limit_up"]))

    def test_absent_list_date_never_infers_a_new_listing(self):
        rows = [bar("20240301", "600519", 99.0, 100.0, 98.0, 100.0, 100.0, 1000)]
        self.assertFalse(bool(panel_from(rows).iloc[0]["new_listing"]))

    def test_window_closes_after_new_listing_days(self):
        rows = [
            bar("2024030%d" % d, "301999", 30.0, 31.0, 29.0, 30.0, 30.0, 1000,
                list_date="20240301")
            for d in range(1, 6)
        ]
        panel = panel_from(rows, new_listing_days=2).set_index("date")
        self.assertTrue(bool(panel.loc["20240302", "new_listing"]))
        self.assertFalse(bool(panel.loc["20240303", "new_listing"]))


class AdjustedPriceGuard(unittest.TestCase):
    def _write(self, price_type):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                             encoding="utf-8")
        pd.DataFrame([{**bar("20240301", "600519", 99.0, 100.0, 98.0, 100.0,
                             100.0, 1000), "price_type": price_type}]).to_csv(
            handle.name, index=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_adjusted_prices_are_refused(self):
        path = self._write("post")
        with self.assertRaises(ValueError) as ctx:
            builder.load_bars(path)
        self.assertIn("adjusted", str(ctx.exception))

    def test_raw_prices_pass_and_the_override_works(self):
        self.assertEqual(len(builder.load_bars(self._write("raw"))), 1)
        self.assertEqual(
            len(builder.load_bars(self._write("post"), allow_adjusted=True)), 1)

    def test_duplicate_bars_are_rejected(self):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                             encoding="utf-8")
        row = bar("20240301", "600519", 99.0, 100.0, 98.0, 100.0, 100.0, 1000)
        pd.DataFrame([row, row]).to_csv(handle.name, index=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        with self.assertRaises(ValueError):
            builder.load_bars(handle.name)


class InventoryAndSettlement(unittest.TestCase):
    def _normal_panel(self):
        rows = [
            bar("20240301", "600519", 99.0, 101.0, 98.0, 100.0, 100.0, 100000),
            bar("20240302", "600519", 99.0, 101.0, 98.0, 100.0, 100.0, 100000),
        ]
        return panel_from(rows)

    def test_same_day_round_trip_is_a_t1_violation_not_a_naked_short(self):
        trades = trades_from([
            {"date": "20240301", "symbol": "600519", "side": "buy", "notional": 1000},
            {"date": "20240301", "symbol": "600519", "side": "sell", "notional": 1000},
        ])
        report = auditor.audit(trades, self._normal_panel())
        ids = {f["id"] for f in report["findings"]}
        self.assertIn("C2_t1_violation", ids)
        self.assertNotIn("C3_short_without_inventory", ids)
        self.assertEqual(report["metrics"]["t1_violation_count"], 1)

    def test_selling_without_ever_buying_is_a_naked_short(self):
        trades = trades_from([
            {"date": "20240301", "symbol": "600519", "side": "sell", "notional": 1000},
        ])
        report = auditor.audit(trades, self._normal_panel())
        ids = {f["id"] for f in report["findings"]}
        self.assertIn("C3_short_without_inventory", ids)
        self.assertNotIn("C2_t1_violation", ids)

    def test_selling_the_next_day_settles_cleanly(self):
        trades = trades_from([
            {"date": "20240301", "symbol": "600519", "side": "buy", "notional": 1000},
            {"date": "20240302", "symbol": "600519", "side": "sell", "notional": 1000},
        ])
        report = auditor.audit(trades, self._normal_panel())
        ids = {f["id"] for f in report["findings"]}
        self.assertNotIn("C2_t1_violation", ids)
        self.assertNotIn("C3_short_without_inventory", ids)
        self.assertEqual(report["status"], "pass")

    def test_an_oversized_sell_splits_into_t1_and_short_parts(self):
        trades = trades_from([
            {"date": "20240301", "symbol": "600519", "side": "buy", "notional": 400},
            {"date": "20240301", "symbol": "600519", "side": "sell", "notional": 1000},
        ])
        report = auditor.audit(trades, self._normal_panel())
        ids = {f["id"] for f in report["findings"]}
        self.assertEqual(ids & {"C2_t1_violation", "C3_short_without_inventory"},
                         {"C2_t1_violation", "C3_short_without_inventory"})


class ExecutionConstraints(unittest.TestCase):
    def test_a_price_beyond_the_band_zeroes_the_fill(self):
        rows = [bar("20240301", "600123", 8.0, 8.2, 7.9, 8.1, 8.0, 100000, is_st=1)]
        trades = trades_from([
            {"date": "20240301", "symbol": "600123", "side": "buy",
             "price": 8.90, "notional": 1000},
        ])
        report = auditor.audit(trades, panel_from(rows))
        self.assertEqual(report["metrics"]["price_outside_limit_count"], 1)
        self.assertEqual(report["ledger"][0]["fill_ratio"], 0.0)

    def test_participation_cap_scales_the_fill_proportionally(self):
        rows = [bar("20240301", "600519", 99.0, 101.0, 98.0, 100.0, 100.0,
                    100000, amount=1_000_000)]
        trades = trades_from([
            {"date": "20240301", "symbol": "600519", "side": "buy", "notional": 500_000},
        ])
        report = auditor.audit(trades, panel_from(rows), participation=0.1)
        # capacity = 0.1 * 1_000_000 = 100_000 out of a 500_000 order.
        self.assertAlmostEqual(report["ledger"][0]["fill_ratio"], 0.2)
        self.assertIn("M4_participation_breach", {f["id"] for f in report["findings"]})

    def test_fill_ratio_is_the_minimum_of_the_three_layers(self):
        rows = [bar("20240301", "600519", 108.0, 110.0, 104.0, 110.0, 100.0,
                    100000, amount=1_000_000)]
        trades = trades_from([
            {"date": "20240301", "symbol": "600519", "side": "buy", "notional": 500_000},
        ])
        report = auditor.audit(trades, panel_from(rows, limit_open_fill=0.3),
                               participation=0.1)
        # market 0.30 vs participation 0.20 -> min is 0.20, not the product.
        self.assertAlmostEqual(report["ledger"][0]["fill_ratio"], 0.2)

    def test_trading_on_a_suspension_day_fills_nothing(self):
        rows = [bar("20240301", "000001", 0.0, 0.0, 0.0, 10.0, 10.0, 0, amount=0.0)]
        trades = trades_from([
            {"date": "20240301", "symbol": "000001", "side": "buy", "notional": 1000},
        ])
        report = auditor.audit(trades, panel_from(rows))
        self.assertIn("H3_trade_on_halted_day", {f["id"] for f in report["findings"]})
        self.assertEqual(report["ledger"][0]["fill_ratio"], 0.0)


class ReportContract(unittest.TestCase):
    def test_phantom_alpha_share_is_the_blocked_share_of_headline_pnl(self):
        rows = [
            bar("20240301", "600519", 110.0, 110.0, 110.0, 110.0, 100.0, 900),
            bar("20240302", "600519", 99.0, 101.0, 98.0, 100.0, 110.0, 100000),
        ]
        trades = trades_from([
            # blocked (locked limit up) and clean, equal size and equal return
            {"date": "20240301", "symbol": "600519", "side": "buy",
             "notional": 1000, "forward_return": 0.10},
            {"date": "20240302", "symbol": "600519", "side": "buy",
             "notional": 1000, "forward_return": 0.10},
        ])
        report = auditor.audit(trades, panel_from(rows))
        self.assertAlmostEqual(report["metrics"]["phantom_alpha_share"], 0.5)
        self.assertEqual(report["status"], "fail")  # 0.5 > default fail_decay 0.30

    def test_thin_coverage_wins_over_every_other_status(self):
        rows = [bar("20240301", "600519", 99.0, 101.0, 98.0, 100.0, 100.0, 100000)]
        trades = trades_from([
            {"date": "20240301", "symbol": "600519", "side": "buy", "notional": 1000},
            {"date": "20240301", "symbol": "999999", "side": "buy", "notional": 1000},
        ])
        report = auditor.audit(trades, panel_from(rows))
        self.assertEqual(report["status"], "insufficient-evidence")
        self.assertIn("E1_missing_state", {f["id"] for f in report["findings"]})

    def test_evidence_indices_point_at_original_input_rows(self):
        rows = [
            bar("20240301", "600519", 99.0, 101.0, 98.0, 100.0, 100.0, 100000),
            bar("20240301", "000001", 0.0, 0.0, 0.0, 10.0, 10.0, 0, amount=0.0),
        ]
        trades = trades_from([
            {"date": "20240301", "symbol": "600519", "side": "buy", "notional": 1000},
            {"date": "20240301", "symbol": "000001", "side": "buy", "notional": 1000},
        ])
        report = auditor.audit(trades, panel_from(rows))
        halt = [f for f in report["findings"] if f["id"] == "H3_trade_on_halted_day"][0]
        self.assertEqual(halt["evidence"][0]["trade_index"], 1)

    def test_parameters_outside_their_domain_are_rejected(self):
        rows = [bar("20240301", "600519", 99.0, 101.0, 98.0, 100.0, 100.0, 100000)]
        panel = panel_from(rows)
        trades = trades_from([
            {"date": "20240301", "symbol": "600519", "side": "buy", "notional": 1000},
        ])
        for kwargs in ({"participation": 0.0}, {"participation": 1.5},
                       {"fail_decay": -0.1}, {"min_coverage": 2.0}):
            with self.assertRaises(ValueError):
                auditor.audit(trades, panel, **kwargs)
        with self.assertRaises(ValueError):
            builder.build_panel(builder.load_bars_frame(pd.DataFrame(rows)),
                                limit_open_fill=1.4)

    def test_unrecognized_side_values_fail_loudly(self):
        with self.assertRaises(ValueError):
            auditor.load_trades_frame(pd.DataFrame([
                {"date": "20240301", "symbol": "600519", "side": "hold",
                 "notional": 1000},
            ])).pipe(lambda df: auditor.audit(df, pd.DataFrame()))

    def test_chinese_side_labels_are_accepted(self):
        self.assertEqual(auditor.normalize_side("买入"), "buy")
        self.assertEqual(auditor.normalize_side("卖出"), "sell")
        self.assertEqual(auditor.normalize_side(" BUY "), "buy")
        self.assertIsNone(auditor.normalize_side("hold"))


class ContractValidator(unittest.TestCase):
    def _demo_report(self):
        bars = builder.load_bars_frame(builder.demo_bars())
        panel = builder.build_panel(bars, min_amount=5e7)
        return auditor.audit(auditor.load_trades_frame(auditor.demo_trades()), panel)

    def test_the_demo_report_satisfies_its_own_contract(self):
        errors, _ = contract.validate(self._demo_report())
        self.assertEqual(errors, [])

    def test_a_high_severity_finding_without_evidence_is_rejected(self):
        report = self._demo_report()
        report["findings"][0]["evidence"] = []
        errors, _ = contract.validate(report)
        self.assertTrue(any("without locatable evidence" in e for e in errors))

    def test_pass_with_outstanding_findings_is_rejected(self):
        report = self._demo_report()
        report["status"] = "pass"
        errors, _ = contract.validate(report)
        self.assertTrue(any("status=pass but findings" in e for e in errors))

    def test_thin_coverage_reported_as_pass_is_rejected(self):
        report = self._demo_report()
        report["metrics"]["state_coverage"] = 0.4
        report["status"] = "warning"
        errors, _ = contract.validate(report)
        self.assertTrue(any("below" in e for e in errors))

    def test_missing_top_level_keys_are_rejected(self):
        errors, _ = contract.validate({"status": "pass"})
        self.assertTrue(errors)


class Determinism(unittest.TestCase):
    def test_the_demo_pipeline_is_byte_stable(self):
        def run():
            bars = builder.load_bars_frame(builder.demo_bars())
            panel = builder.build_panel(bars, min_amount=5e7)
            report = auditor.audit(auditor.load_trades_frame(auditor.demo_trades()),
                                   panel)
            return json.dumps(report, ensure_ascii=False, sort_keys=True)

        self.assertEqual(run(), run())

    def test_the_demo_fixture_trips_every_documented_rule(self):
        bars = builder.load_bars_frame(builder.demo_bars())
        panel = builder.build_panel(bars, min_amount=5e7)
        report = auditor.audit(auditor.load_trades_frame(auditor.demo_trades()), panel)
        fired = {f["id"] for f in report["findings"]}
        expected = set(auditor.RULES) - {"E1_missing_state", "M2_sell_on_limit_down_open"}
        self.assertEqual(expected - fired, set(),
                         "demo no longer covers: %s" % sorted(expected - fired))
        self.assertEqual(report["status"], "fail")


if __name__ == "__main__":
    unittest.main()
