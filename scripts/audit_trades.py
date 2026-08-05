#!/usr/bin/env python3
"""Audit a backtest / signal trade log against A-share tradability constraints.

Step 2 of the audit. Replays the trade log against the state panel produced by
`build_tradability_panel.py` and separates *paper* PnL from *executable* PnL.

Constraints enforced, in the order a real account would hit them:

  1. Inventory & T+1 -- shares bought on day D are frozen until D+1. A sell that
     exceeds the settled inventory is either a T+1 violation (today's buy being
     sold today) or a naked short (no inventory at all; A-share cash accounts
     cannot do this).
  2. Price validity -- an intended price outside [limit_down, limit_up] can
     never be filled; that is a matching-engine defect in the backtest.
  3. Market state -- suspension, locked limit up (cannot buy in), locked limit
     down (cannot sell out), limit bars that opened intraday (partial).
  4. Participation -- an order larger than `participation x daily turnover`
     cannot realistically be absorbed in one session.

The residual after all four is `fill_ratio`. Applying it to the trade's forward
return yields `phantom_alpha_share`: the share of headline PnL that comes from
trades the market would not have given you.

Usage:
    python audit_trades.py --demo
    python audit_trades.py --trades trades.csv --panel panel.csv --out audit.json
    python audit_trades.py --trades t.csv --panel p.csv --ledger ledger.csv
"""
from __future__ import annotations

import argparse
import json
import sys

TRADE_REQUIRED = ["date", "symbol", "side"]
BUY_WORDS = {"buy", "b", "long", "open", "1", "买入", "买"}
SELL_WORDS = {"sell", "s", "short", "close", "-1", "卖出", "卖"}

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

RULES = {
    "C1_price_outside_limit": (
        "critical",
        "成交价越过当日涨跌停价，真实撮合不可能发生",
        "修正回测撮合器：限价单必须在 [limit_down, limit_up] 内，越界单应作废而非成交",
    ),
    "C2_t1_violation": (
        "critical",
        "卖出量超过已结算库存，且当日有买入——违反 T+1",
        "在回测引擎中把当日买入标记为冻结，次一交易日才计入可卖库存",
    ),
    "C3_short_without_inventory": (
        "critical",
        "卖出量超过全部持仓，A 股普通账户无法裸卖空",
        "限制卖出量不超过持仓；若确为融券策略，需单独建模券源、费率与可融额度",
    ),
    "H1_buy_on_locked_limit_up": (
        "high",
        "在封死的涨停板上买入——买单排不进队列",
        "把封死涨停日的买入信号顺延到下一可交易日，并按次日开盘价重估收益",
    ),
    "H2_sell_on_locked_limit_down": (
        "high",
        "在封死的跌停板上卖出——卖单无法成交，实际被套牢",
        "把跌停日的卖出顺延，并让持仓继续承担后续跌幅，不得按跌停价直接出清",
    ),
    "H3_trade_on_halted_day": (
        "high",
        "在停牌日交易",
        "停牌日不产生交易；复牌日按开盘价重估，并检查停牌期间的跳空",
    ),
    "M1_buy_on_limit_up_open": (
        "medium",
        "在盘中开板的涨停日买入——只能部分成交",
        "按 limit_open_fill 折算成交量，并对该参数做敏感性分析",
    ),
    "M2_sell_on_limit_down_open": (
        "medium",
        "在盘中开板的跌停日卖出——只能部分成交",
        "按 limit_open_fill 折算成交量，并对该参数做敏感性分析",
    ),
    "M3_new_listing_window": (
        "medium",
        "在新股上市窗口内交易，涨跌幅与流动性均不适用常规规则",
        "把上市 N 日内的标的排除出可交易域，或单独建模次新股的成交假设",
    ),
    "M4_participation_breach": (
        "medium",
        "单笔委托金额超过当日成交额的可参与上限",
        "按参与率拆单到多个交易日，并计入额外的冲击成本与信号衰减",
    ),
    "L1_st_universe": (
        "low",
        "交易风险警示（ST/*ST）标的，流动性差、退市风险高；2026-07-06 前主板还额外收窄至 5%",
        "确认策略允许 ST 域；若不允许，在选股阶段过滤而非在成交阶段发现",
    ),
    "L2_low_liquidity": (
        "low",
        "标的当日成交额低于设定阈值",
        "提高流动性门槛或降低单票权重上限",
    ),
    "E1_missing_state": (
        "info",
        "交易日在状态面板中缺失，无法判定可交易性",
        "补齐行情面板覆盖区间后重跑；在此之前不要把该笔交易计入通过项",
    ),
}


def normalize_side(value):
    text = str(value).strip().lower()
    if text in BUY_WORDS:
        return "buy"
    if text in SELL_WORDS:
        return "sell"
    return None


def _read_table(path, dtypes):
    import pandas as pd
    if str(path).endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype=dtypes)


def normalize_trades(df):
    """Validate and normalize a trade frame. Shared by the file and in-memory paths."""
    import pandas as pd
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in TRADE_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError("trades missing required columns: %s" % missing)
    if "notional" not in df.columns and "shares" not in df.columns:
        raise ValueError("trades need at least one of: notional, shares")

    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["date"] = df["date"].astype(str).str.strip().str.replace("-", "", regex=False)

    raw_side = df["side"]
    df["side"] = raw_side.map(normalize_side)
    bad = df["side"].isna()
    if bool(bad.any()):
        offenders = sorted({str(v) for v in raw_side[bad].unique()})[:5]
        raise ValueError(
            "%d trades have an unrecognized side value (e.g. %s); expected "
            "buy/sell, 买入/卖出 or 1/-1" % (int(bad.sum()), offenders)
        )

    for col in ("price", "notional", "shares", "forward_return"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


def load_trades(path):
    return normalize_trades(_read_table(path, {"symbol": str, "date": str}))


def load_panel(path):
    import pandas as pd
    df = _read_table(path, {"symbol": str, "date": str})
    df.columns = [str(c).strip().lower() for c in df.columns]
    need = ["date", "symbol", "state", "buy_capacity", "sell_capacity"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError("panel missing required columns: %s" % missing)
    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["date"] = df["date"].astype(str).str.strip().str.replace("-", "", regex=False)
    return df


def audit(trades, panel, participation=0.1, fail_decay=0.30, min_coverage=0.90):
    """Replay the trade log under A-share constraints. Returns a report dict."""
    import numpy as np

    if not (0.0 < participation <= 1.0):
        raise ValueError("--participation must be within (0, 1]")
    if not (0.0 <= fail_decay <= 1.0):
        raise ValueError("--fail-decay must be within [0, 1]")
    if not (0.0 <= min_coverage <= 1.0):
        raise ValueError("--min-coverage must be within [0, 1]")

    states = {(r["symbol"], r["date"]): r for r in panel.to_dict("records")}

    df = trades.copy()
    # size unit: shares when supplied, else notional. Both are consumed by the
    # same inventory ledger, so the log must be internally consistent.
    if "shares" in df.columns and df["shares"].notna().any():
        df["size"] = df["shares"]
        size_unit = "shares"
    else:
        df["size"] = df["notional"]
        size_unit = "notional"
    if "notional" in df.columns:
        df["notional_used"] = df["notional"]
    else:
        df["notional_used"] = df["size"]
    df["size"] = df["size"].astype(float).abs()
    df["notional_used"] = df["notional_used"].astype(float).abs()

    # Deterministic intraday ordering: sells settle against yesterday's
    # inventory before today's buys are added to it.
    order = {"sell": 0, "buy": 1}
    df["_orig"] = range(len(df))
    df["_ord"] = df["side"].map(order)
    df = df.sort_values(["date", "_ord", "symbol"], kind="mergesort").reset_index(drop=True)

    # Same-day buy volume per symbol. Because sells are replayed first, `frozen`
    # is still empty when a same-day sell arrives, so T+1 has to be diagnosed
    # from this lookahead rather than from the running ledger -- otherwise every
    # same-day round trip would be mislabelled as a naked short.
    same_day_buys = (
        df[df["side"] == "buy"].groupby(["date", "symbol"])["size"].sum().to_dict()
    )

    settled = {}
    frozen = {}
    current_date = None
    ledger = []
    hits = {}

    def flag(rule, row_idx, detail):
        hits.setdefault(rule, []).append({"trade_index": int(row_idx), **detail})

    for _, t in df.iterrows():
        if t["date"] != current_date:
            for sym, qty in list(frozen.items()):
                settled[sym] = settled.get(sym, 0.0) + qty
            frozen = {}
            current_date = t["date"]

        sym, side, size = t["symbol"], t["side"], float(t["size"])
        row_id = int(t["_orig"])
        state = states.get((sym, t["date"]))
        rules_hit = []

        if state is None:
            flag("E1_missing_state", row_id, {"symbol": sym, "date": t["date"]})
            ledger.append({
                "trade_row": row_id,
                "date": t["date"], "symbol": sym, "side": side, "size": size,
                "state": "unknown", "inventory_ratio": None, "market_ratio": None,
                "fill_ratio": None, "filled_size": 0.0, "rules": ["E1_missing_state"],
            })
            continue

        # --- 1. inventory & T+1 --------------------------------------------
        available = settled.get(sym, 0.0)
        held_today = frozen.get(sym, 0.0)
        if side == "sell":
            executable = min(size, available)
            excess = size - executable
            if excess > 1e-12:
                todays_buys = float(same_day_buys.get((t["date"], sym), 0.0)) + held_today
                t1_part = min(excess, todays_buys)
                short_part = excess - t1_part
                if t1_part > 1e-12:
                    rules_hit.append("C2_t1_violation")
                    flag("C2_t1_violation", row_id, {
                        "symbol": sym, "date": t["date"],
                        "requested": size, "settled_available": available,
                        "bought_same_day": todays_buys,
                        "t1_locked_size": round(t1_part, 6),
                    })
                if short_part > 1e-12:
                    rules_hit.append("C3_short_without_inventory")
                    flag("C3_short_without_inventory", row_id, {
                        "symbol": sym, "date": t["date"],
                        "requested": size, "settled_available": available,
                        "uncovered_size": round(short_part, 6),
                    })
            inventory_ratio = executable / size if size > 0 else 0.0
        else:
            inventory_ratio = 1.0

        # --- 2. price validity ---------------------------------------------
        price = t.get("price")
        up, down = state.get("limit_up"), state.get("limit_down")
        if price is not None and np.isfinite(price):
            if up is not None and np.isfinite(up) and price > up + 1e-9:
                rules_hit.append("C1_price_outside_limit")
                flag("C1_price_outside_limit", row_id, {
                    "symbol": sym, "date": t["date"], "price": float(price),
                    "limit_up": float(up), "direction": "above_limit_up",
                })
            elif down is not None and np.isfinite(down) and price < down - 1e-9:
                rules_hit.append("C1_price_outside_limit")
                flag("C1_price_outside_limit", row_id, {
                    "symbol": sym, "date": t["date"], "price": float(price),
                    "limit_down": float(down), "direction": "below_limit_down",
                })

        # --- 3. market state ------------------------------------------------
        market_ratio = float(state["buy_capacity"] if side == "buy" else state["sell_capacity"])
        st_state = state["state"]
        if st_state == "halted":
            rules_hit.append("H3_trade_on_halted_day")
        elif side == "buy" and st_state == "limit_up_locked":
            rules_hit.append("H1_buy_on_locked_limit_up")
        elif side == "sell" and st_state == "limit_down_locked":
            rules_hit.append("H2_sell_on_locked_limit_down")
        elif side == "buy" and st_state == "limit_up_open":
            rules_hit.append("M1_buy_on_limit_up_open")
        elif side == "sell" and st_state == "limit_down_open":
            rules_hit.append("M2_sell_on_limit_down_open")

        if bool(state.get("new_listing")):
            rules_hit.append("M3_new_listing_window")
        if bool(state.get("is_st")):
            rules_hit.append("L1_st_universe")
        if bool(state.get("low_liquidity")):
            rules_hit.append("L2_low_liquidity")

        # --- 4. participation -----------------------------------------------
        participation_ratio = 1.0
        amount = state.get("amount")
        notional = float(t["notional_used"]) if np.isfinite(t["notional_used"]) else None
        if amount is not None and np.isfinite(amount) and amount > 0 and notional:
            capacity = participation * float(amount)
            if notional > capacity:
                participation_ratio = capacity / notional
                rules_hit.append("M4_participation_breach")
                flag("M4_participation_breach", row_id, {
                    "symbol": sym, "date": t["date"], "notional": notional,
                    "daily_amount": float(amount), "capacity": capacity,
                })

        if "C1_price_outside_limit" in rules_hit:
            market_ratio = 0.0

        fill_ratio = min(inventory_ratio, market_ratio, participation_ratio)
        filled = size * fill_ratio

        for rule in rules_hit:
            if rule not in ("C2_t1_violation", "C3_short_without_inventory",
                            "C1_price_outside_limit", "M4_participation_breach"):
                flag(rule, row_id, {"symbol": sym, "date": t["date"], "state": st_state})

        if side == "buy":
            frozen[sym] = frozen.get(sym, 0.0) + filled
        else:
            settled[sym] = max(0.0, settled.get(sym, 0.0) - filled)

        ledger.append({
            "trade_row": row_id,
            "date": t["date"], "symbol": sym, "side": side, "size": size,
            "state": st_state, "inventory_ratio": round(inventory_ratio, 6),
            "market_ratio": round(market_ratio, 6),
            "participation_ratio": round(participation_ratio, 6),
            "fill_ratio": round(fill_ratio, 6), "filled_size": round(filled, 6),
            "rules": rules_hit,
        })

    return _assemble(df, ledger, hits, states, participation, fail_decay,
                     min_coverage, size_unit)


def _assemble(df, ledger, hits, states, participation, fail_decay,
              min_coverage, size_unit):
    import numpy as np

    total = len(ledger)
    known = [row for row in ledger if row["fill_ratio"] is not None]
    coverage = (len(known) / total) if total else 0.0

    notional = df["notional_used"].astype(float).fillna(0.0).to_numpy()
    fills = np.array([r["fill_ratio"] if r["fill_ratio"] is not None else 0.0
                      for r in ledger], dtype=float)
    notional_sum = float(notional.sum())
    fill_weighted = float((notional * fills).sum() / notional_sum) if notional_sum > 0 else 0.0
    blocked = [r for r in known if r["fill_ratio"] < 1.0]
    fully_blocked = [r for r in known if r["fill_ratio"] <= 0.0]

    buys = [r for r in known if r["side"] == "buy"]
    sells = [r for r in known if r["side"] == "sell"]
    limit_up_buys = [r for r in buys if r["state"] in ("limit_up_locked", "limit_up_open")]
    limit_down_sells = [r for r in sells
                        if r["state"] in ("limit_down_locked", "limit_down_open")]

    metrics = {
        "trades_total": total,
        "state_coverage": round(coverage, 6),
        "blocked_trade_count": len(blocked),
        "fully_blocked_trade_count": len(fully_blocked),
        "blocked_notional_share": round(
            float(notional[[i for i, r in enumerate(ledger)
                            if r["fill_ratio"] is None or r["fill_ratio"] < 1.0]].sum()
                  / notional_sum) if notional_sum > 0 else 0.0, 6),
        "fill_weighted_ratio": round(fill_weighted, 6),
        "limit_up_entry_rate": round(len(limit_up_buys) / len(buys), 6) if buys else 0.0,
        "limit_down_exit_trap_rate": round(len(limit_down_sells) / len(sells), 6) if sells else 0.0,
        "halt_trade_rate": round(
            len([r for r in known if r["state"] == "halted"]) / len(known), 6) if known else 0.0,
        "t1_violation_count": len(hits.get("C2_t1_violation", [])),
        "short_without_inventory_count": len(hits.get("C3_short_without_inventory", [])),
        "price_outside_limit_count": len(hits.get("C1_price_outside_limit", [])),
    }

    # --- return attribution ------------------------------------------------
    attribution = None
    if "forward_return" in df.columns and df["forward_return"].notna().any():
        fwd = df["forward_return"].astype(float).fillna(0.0).to_numpy()
        sign = np.array([1.0 if r["side"] == "buy" else -1.0 for r in ledger])
        gross = float((sign * notional * fwd).sum())
        net = float((sign * notional * fills * fwd).sum())
        phantom = gross - net
        attribution = {
            "gross_signal_pnl": round(gross, 6),
            "executable_pnl": round(net, 6),
            "phantom_pnl": round(phantom, 6),
            "phantom_alpha_share": round(phantom / gross, 6) if abs(gross) > 1e-12 else None,
            "note": "signed by side (buy=+, sell=-); notional-weighted forward return",
        }
        metrics["phantom_alpha_share"] = attribution["phantom_alpha_share"]

    # --- findings -----------------------------------------------------------
    findings = []
    for rule, events in hits.items():
        severity, impact, fix = RULES[rule]
        idxs = [e["trade_index"] for e in events]
        share = float(notional[idxs].sum() / notional_sum) if notional_sum > 0 else 0.0
        findings.append({
            "id": rule,
            "severity": severity,
            "count": len(events),
            "notional_share": round(share, 6),
            "impact": impact,
            "recommended_fix": fix,
            "evidence": events[:5],
        })
    findings.sort(key=lambda f: (SEVERITY_ORDER.index(f["severity"]), -f["count"]))

    # --- status -------------------------------------------------------------
    severities = {f["severity"] for f in findings}
    decay = attribution["phantom_alpha_share"] if attribution else None
    if coverage < min_coverage:
        status = "insufficient-evidence"
    elif "critical" in severities:
        status = "fail"
    elif decay is not None and decay > fail_decay:
        status = "fail"
    elif severities & {"high", "medium"}:
        status = "warning"
    elif severities:
        status = "warning"
    else:
        status = "pass"

    limitations = [
        "limit_open_fill 与 participation 是情景假设，不是撮合保证；结论必须做敏感性分析。",
        "size 单位为 %s；交易日志内部单位不一致时库存与 T+1 判定会失真。" % size_unit,
        "库存台账从零开始重放，期初已有持仓需以期初买入行补齐，否则会误判为裸卖空。",
        "未建模：融券券源、大宗交易、协议转让、ETF 申赎与盘后固定价格交易。",
        "自 2026-07-06 起盘后固定价格交易适用范围扩展至全部 A 股与 ETF，按收盘价成交。"
        "封死涨停日的收盘价即涨停价，理论上该笔买入仍可能在盘后时段撮合到——本 Skill 不建模"
        "盘后对手盘，因此该日之后的 H1/H2 判定偏保守（可能高估阻断）。",
    ]
    if status == "insufficient-evidence":
        limitations.append("状态面板覆盖率 %.1f%% 低于阈值，不得据此下定量结论。"
                           % (coverage * 100))

    next_actions = []
    if metrics["t1_violation_count"] or metrics["short_without_inventory_count"]:
        next_actions.append("先修回测引擎的库存与 T+1 约束，再重跑收益归因。")
    if metrics["price_outside_limit_count"]:
        next_actions.append("修正撮合价钳制逻辑，越过涨跌停的成交必须作废。")
    if metrics["limit_up_entry_rate"] > 0.05:
        next_actions.append("涨停买入占比 %.1f%%，把信号顺延一日后重估策略。"
                            % (metrics["limit_up_entry_rate"] * 100))
    if decay is not None and decay > 0.1:
        next_actions.append("可成交收益较账面收益衰减 %.1f%%，以衰减后曲线作为决策依据。"
                            % (decay * 100))
    if not next_actions:
        next_actions.append("未发现阻断性约束；保留本次参数与数据快照以便复现。")

    return {
        "status": status,
        "input_summary": {
            "trades": total,
            "symbols": int(df["symbol"].nunique()),
            "date_range": [str(df["date"].min()), str(df["date"].max())] if total else [],
            "panel_rows": len(states),
            "size_unit": size_unit,
        },
        "assumptions": {
            "participation": participation,
            "fail_decay": fail_decay,
            "min_coverage": min_coverage,
            "intraday_order": "sells settle against prior-day inventory before same-day buys",
            "initial_inventory": "flat",
        },
        "metrics": metrics,
        "return_attribution": attribution,
        "findings": findings,
        "limitations": limitations,
        "next_actions": next_actions,
        "ledger": ledger,
    }


def demo_trades():
    """Trade log designed to trip every rule exactly once, deterministically."""
    import pandas as pd
    rows = [
        # clean baseline
        ("20240301", "600519", "buy", 99.0, 1_000_000, 0.04),
        # buying a locked one-word limit up -> impossible
        ("20240304", "600519", "buy", 110.0, 2_000_000, 0.05),
        # limit up that opened intraday -> partial fill
        ("20240305", "300750", "buy", 239.0, 1_500_000, 0.03),
        # T+1: bought and sold 000001 on the same day
        ("20240304", "000001", "buy", 10.1, 500_000, 0.01),
        ("20240304", "000001", "sell", 10.2, 500_000, -0.02),
        # trading on a suspension day
        ("20240306", "000001", "buy", 10.3, 400_000, 0.02),
        # locked limit down exit -> trapped
        ("20240301", "688981", "buy", 50.0, 800_000, 0.02),
        ("20240307", "688981", "sell", 40.0, 800_000, -0.15),
        # price above the ST 5% band -> matching-engine defect
        ("20240308", "600123", "buy", 8.90, 300_000, 0.01),
        # order far larger than the day's turnover
        ("20240305", "600123", "buy", 8.10, 40_000_000, 0.01),
        # new-listing window
        ("20240306", "301999", "buy", 52.0, 600_000, -0.05),
        # naked short: never held 600519 enough to sell this much
        ("20240308", "600519", "sell", 111.0, 9_000_000, -0.01),
    ]
    return pd.DataFrame(rows, columns=[
        "date", "symbol", "side", "price", "notional", "forward_return",
    ])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit a trade log against A-share tradability constraints."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--trades", help="trade/signal log CSV or parquet")
    source.add_argument("--demo", action="store_true", help="use the built-in fixture")
    parser.add_argument("--panel", help="tradability panel from build_tradability_panel.py")
    parser.add_argument("--participation", type=float, default=0.1,
                        help="max share of daily turnover one order may take (default 0.1)")
    parser.add_argument("--fail-decay", type=float, default=0.30,
                        help="phantom_alpha_share above this fails the audit (default 0.30)")
    parser.add_argument("--min-coverage", type=float, default=0.90,
                        help="panel coverage below this returns insufficient-evidence")
    parser.add_argument("--ledger", help="optional per-trade CSV output path")
    parser.add_argument("--out", help="write the JSON report here; default stdout")
    parser.add_argument("--no-ledger-in-json", action="store_true",
                        help="drop the per-trade ledger from the JSON report")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.demo:
            import build_tradability_panel as builder
            bars = builder.load_bars_frame(builder.demo_bars())
            panel = builder.build_panel(bars, min_amount=5e7)
            trades = load_trades_frame(demo_trades())
        else:
            if not args.panel:
                raise ValueError("--panel is required unless --demo is used")
            trades = load_trades(args.trades)
            panel = load_panel(args.panel)
        report = audit(
            trades, panel,
            participation=args.participation,
            fail_decay=args.fail_decay,
            min_coverage=args.min_coverage,
        )
    except (ValueError, OSError, ImportError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.ledger:
        import pandas as pd
        led = pd.DataFrame(report["ledger"])
        led["rules"] = led["rules"].map(lambda r: "|".join(r))
        led.to_csv(args.ledger, index=False)

    payload = dict(report)
    if args.no_ledger_in_json:
        payload.pop("ledger", None)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print("status=%s phantom_alpha_share=%s -> %s" % (
            report["status"],
            report["metrics"].get("phantom_alpha_share"),
            args.out,
        ))
    else:
        print(text)
    return 0


def load_trades_frame(df):
    """Normalize an in-memory trade frame. Alias of normalize_trades, kept so the
    demo path and the file path cannot drift apart in validation strictness."""
    return normalize_trades(df)


if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    raise SystemExit(main())
