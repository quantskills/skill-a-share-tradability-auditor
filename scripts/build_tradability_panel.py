#!/usr/bin/env python3
"""Turn a raw A-share daily bar panel into a date x symbol *tradability state* panel.

This is step 1 of the tradability audit. It answers, for every (date, symbol),
the question a backtest engine almost never asks: **could this bar actually be
traded, and in which direction?**

It reconstructs, per bar:
  - board (sh_main / sz_main / chinext / star / bse) and the price-limit
    percentage applicable **on that bar's date** (10% / 20% / 30%; main-board
    risk-warning names were 5% until 2026-07-06 and 10% from then on),
  - limit_up / limit_down prices (ROUND_HALF_UP to 0.01, exchange convention),
  - a primary state: normal / halted / limit_up_locked / limit_up_open /
    limit_down_locked / limit_down_open,
  - buy_capacity and sell_capacity in [0, 1] -- the fraction of an intended
    order that this bar can plausibly absorb,
  - flags: is_st, new_listing, low_liquidity, limit_unreliable.

Suspension is inferred from the panel's own trading calendar: a symbol that is
missing on a date where the rest of the universe trades, and which lies between
that symbol's first and last observed bar, is treated as `halted`.

CRITICAL: price limits must be computed on **unadjusted** prices. Feeding a
back/forward-adjusted panel silently produces wrong limit prices and therefore
a wrong audit. The script refuses adjusted input unless --allow-adjusted.

Usage:
    python build_tradability_panel.py --demo --out panel.csv
    python build_tradability_panel.py --bars bars.csv --out panel.csv
    python build_tradability_panel.py --bars bars.parquet --limit-open-fill 0.2
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal, ROUND_HALF_UP

REQUIRED_COLUMNS = ["date", "symbol", "open", "high", "low", "close", "volume"]
OPTIONAL_COLUMNS = [
    "pre_close", "amount", "is_st", "list_date", "limit_up", "limit_down",
    "price_type",
]

# Price-limit percentage by board, AS A HISTORY -- the rules change, and a
# backtest audit spans history, so the band must be resolved per bar date rather
# than pinned to whatever happens to be current today.
#
# Each entry is (effective_from_YYYYMMDD, {"normal": pct, "st": pct}), ascending.
#
# Known change: the SSE/SZSE main-board risk-warning band was 5% from 1998-04-22
# until 2026-07-06, when the revised 《交易规则》 (published 2026-04-24) raised it
# to 10% to match ordinary main-board stocks. ChiNext / STAR / BSE never halved
# their band under risk warning and are unaffected.
ST_MAIN_BOARD_WIDENED = "20260706"

BOARD_RULE_HISTORY = {
    "sh_main": [
        ("00000000", {"normal": 0.10, "st": 0.05}),
        (ST_MAIN_BOARD_WIDENED, {"normal": 0.10, "st": 0.10}),
    ],
    "sz_main": [
        ("00000000", {"normal": 0.10, "st": 0.05}),
        (ST_MAIN_BOARD_WIDENED, {"normal": 0.10, "st": 0.10}),
    ],
    "chinext": [("00000000", {"normal": 0.20, "st": 0.20})],
    "star": [("00000000", {"normal": 0.20, "st": 0.20})],
    "bse": [("00000000", {"normal": 0.30, "st": 0.30})],
}


def limit_pct_for(board, is_st, date):
    """Price-limit percentage applicable to `board` on `date` (YYYYMMDD).

    Resolving this per date is the whole point: auditing a 2024 sample under the
    2026 band would silently widen every ST bar and hide real limit blocks, while
    auditing a 2027 sample under the old 5% band would invent blocks that never
    happened.
    """
    if board is None:
        return None
    key = "st" if is_st else "normal"
    table = None
    for effective_from, rules in BOARD_RULE_HISTORY[board]:
        if str(date) >= effective_from:
            table = rules
        else:
            break
    return None if table is None else table[key]

STATES = (
    "normal", "halted",
    "limit_up_locked", "limit_up_open",
    "limit_down_locked", "limit_down_open",
)


def infer_board(symbol):
    """Map a 6-digit A-share code (with or without a .SH/.SZ/.BJ suffix) to a board."""
    raw = str(symbol).strip().upper().split(".")[0]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) != 6:
        return None
    p3 = digits[:3]
    if p3 in {"688", "689"}:
        return "star"
    if p3 in {"300", "301", "302"}:
        return "chinext"
    if p3 == "920" or digits[0] in {"4", "8"}:
        return "bse"
    if p3 in {"600", "601", "603", "605"}:
        return "sh_main"
    if p3 in {"000", "001", "002", "003"}:
        return "sz_main"
    return None


def limit_price(pre_close, pct, direction):
    """Exchange rounding: pre_close * (1 +/- pct), ROUND_HALF_UP to 0.01."""
    if pre_close is None or pct is None:
        return None
    try:
        base = Decimal(str(float(pre_close)))
    except (TypeError, ValueError):
        return None
    if not base.is_finite() or base <= 0:
        return None
    factor = Decimal(1) + (Decimal(str(pct)) if direction == "up" else -Decimal(str(pct)))
    return float((base * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _read_table(path):
    import pandas as pd
    if str(path).endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_csv(path, dtype={"symbol": str, "date": str, "list_date": str})


def _truthy(value):
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y", "st", "*st"}


def load_bars(path, allow_adjusted=False):
    import pandas as pd
    df = _read_table(path)
    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("bars missing required columns: %s" % missing)

    if "price_type" in df.columns and not allow_adjusted:
        kinds = {str(v).strip().lower() for v in df["price_type"].dropna().unique()}
        bad = kinds - {"raw", "none", "unadjusted", "nan", ""}
        if bad:
            raise ValueError(
                "price_type=%s looks adjusted; price limits are only valid on "
                "unadjusted prices. Re-fetch raw prices or pass --allow-adjusted "
                "and treat every limit verdict as a proxy." % sorted(bad)
            )

    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["date"] = df["date"].astype(str).str.strip().str.replace("-", "", regex=False)
    for col in ("open", "high", "low", "close", "volume", "pre_close", "amount",
                "limit_up", "limit_down"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "is_st" in df.columns:
        df["is_st"] = df["is_st"].map(_truthy)
    else:
        df["is_st"] = False
    if "list_date" in df.columns:
        df["list_date"] = (
            df["list_date"].astype(str).str.strip().str.replace("-", "", regex=False)
        )
    df = df.sort_values(["symbol", "date"], kind="mergesort").reset_index(drop=True)

    dup = df.duplicated(subset=["date", "symbol"]).sum()
    if dup:
        raise ValueError("bars contain %d duplicated (date, symbol) rows" % int(dup))
    return df


def build_panel(bars, limit_open_fill=0.3, new_listing_days=5,
                min_amount=0.0, price_eps=0.005):
    """Return a date x symbol tradability panel as a DataFrame."""
    import numpy as np
    import pandas as pd

    if not (0.0 <= limit_open_fill <= 1.0):
        raise ValueError("--limit-open-fill must be within [0, 1]")
    if new_listing_days < 0:
        raise ValueError("--new-listing-days must be >= 0")
    if price_eps < 0:
        raise ValueError("--price-eps must be >= 0")

    df = bars.copy()

    # pre_close: prefer the supplied column, else the previous observed close.
    prev_close = df.groupby("symbol")["close"].shift(1)
    if "pre_close" in df.columns:
        df["pre_close_used"] = df["pre_close"].where(df["pre_close"].notna(), prev_close)
        df["pre_close_source"] = np.where(df["pre_close"].notna(), "given", "prev_close")
    else:
        df["pre_close_used"] = prev_close
        df["pre_close_source"] = "prev_close"

    df["board"] = df["symbol"].map(infer_board)
    df["limit_pct"] = [
        limit_pct_for(b, st, d)
        for b, st, d in zip(df["board"], df["is_st"], df["date"])
    ]

    # Trading days observed anywhere in the universe; used to detect suspensions.
    all_dates = sorted(df["date"].unique())
    span = df.groupby("symbol")["date"].agg(["min", "max"])

    # Bars actually present, keyed for O(1) lookup.
    present = set(zip(df["symbol"], df["date"]))

    rows = []
    listed_counter = {}

    def emit(symbol, date, bar):
        board = None if bar is None else bar["board"]
        is_st = bool(bar["is_st"]) if bar is not None else False

        # The new-listing window is only asserted when a list_date is supplied.
        # A symbol simply appearing early in the sample is NOT a new listing --
        # inferring one from panel position would flag every symbol on day one.
        listed_counter[symbol] = listed_counter.get(symbol, 0) + 1
        list_date = None
        if bar is not None and isinstance(bar.get("list_date"), str):
            if bar["list_date"] not in ("", "nan", "None", "NaT"):
                list_date = bar["list_date"]
        if list_date is None:
            new_listing = False
            days_listed = None
        else:
            days_listed = len([d for d in all_dates if list_date <= d <= date])
            new_listing = days_listed <= new_listing_days

        if bar is None:
            rows.append({
                "date": date, "symbol": symbol, "board": board,
                "is_st": is_st, "limit_pct": None,
                "pre_close": None, "limit_up": None, "limit_down": None,
                "close": None, "amount": None,
                "state": "halted", "buy_capacity": 0.0, "sell_capacity": 0.0,
                "new_listing": new_listing, "low_liquidity": True,
                "limit_reliable": False, "bar_present": False,
                "note": "missing bar on a universe trading day (suspension)",
            })
            return

        pct = bar["limit_pct"]
        pre = bar["pre_close_used"]
        up = bar.get("limit_up")
        down = bar.get("limit_down")
        up_source = "given"
        if up is None or (isinstance(up, float) and not np.isfinite(up)):
            up, up_source = limit_price(pre, pct, "up"), "inferred"
        if down is None or (isinstance(down, float) and not np.isfinite(down)):
            down = limit_price(pre, pct, "down")

        # Newly listed shares trade without the ordinary band (registration-system
        # IPOs are unbanded for their first sessions), so a pre_close-derived
        # limit is meaningless there. Drop the inferred prices rather than assert
        # a fake "limit up" on the listing-day pop.
        limit_reliable = up is not None and down is not None and not new_listing
        if new_listing:
            up = down = None
        volume = bar["volume"]
        amount = bar.get("amount")
        halted = (not np.isfinite(volume)) or volume <= 0

        hi, lo, close = bar["high"], bar["low"], bar["close"]
        at_up = up is not None and np.isfinite(close) and close >= up - price_eps
        at_down = down is not None and np.isfinite(close) and close <= down + price_eps
        # "locked" == the bar never traded away from the limit (一字板 / T字板收盘封死).
        locked_up = at_up and np.isfinite(lo) and lo >= up - price_eps
        locked_down = at_down and np.isfinite(hi) and hi <= down + price_eps

        if halted:
            state, buy_cap, sell_cap = "halted", 0.0, 0.0
        elif locked_up:
            state, buy_cap, sell_cap = "limit_up_locked", 0.0, 1.0
        elif at_up:
            state, buy_cap, sell_cap = "limit_up_open", float(limit_open_fill), 1.0
        elif locked_down:
            state, buy_cap, sell_cap = "limit_down_locked", 1.0, 0.0
        elif at_down:
            state, buy_cap, sell_cap = "limit_down_open", 1.0, float(limit_open_fill)
        else:
            state, buy_cap, sell_cap = "normal", 1.0, 1.0

        low_liq = bool(
            min_amount > 0 and amount is not None
            and np.isfinite(amount) and amount < min_amount
        )
        note = ""
        if up_source == "inferred" and pct is None:
            note = "board unknown; price limits not inferred"
        elif bar["pre_close_source"] == "prev_close":
            note = "pre_close derived from previous close"

        rows.append({
            "date": date, "symbol": symbol, "board": board,
            "is_st": is_st, "limit_pct": pct,
            "pre_close": None if pre is None or not np.isfinite(pre) else float(pre),
            "limit_up": up, "limit_down": down,
            "close": float(close) if np.isfinite(close) else None,
            "amount": float(amount) if amount is not None and np.isfinite(amount) else None,
            "state": state, "buy_capacity": buy_cap, "sell_capacity": sell_cap,
            "new_listing": bool(new_listing), "low_liquidity": low_liq,
            "limit_reliable": bool(limit_reliable), "bar_present": True,
            "note": note,
        })

    indexed = {(r["symbol"], r["date"]): r for r in df.to_dict("records")}
    for symbol in sorted(span.index):
        first, last = span.loc[symbol, "min"], span.loc[symbol, "max"]
        for date in all_dates:
            if date < first or date > last:
                continue
            if (symbol, date) in present:
                emit(symbol, date, indexed[(symbol, date)])
            else:
                emit(symbol, date, None)

    panel = pd.DataFrame(rows, columns=[
        "date", "symbol", "board", "is_st", "limit_pct", "pre_close",
        "limit_up", "limit_down", "close", "amount", "state",
        "buy_capacity", "sell_capacity", "new_listing", "low_liquidity",
        "limit_reliable", "bar_present", "note",
    ])
    return panel.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)


def demo_bars():
    """Deterministic 5-symbol x 6-day fixture covering every tradability state."""
    import pandas as pd
    rows = [
        # 600519 sh_main 10%: normal, then a locked one-word limit up.
        ("20240301", "600519", 98.0, 100.5, 97.5, 100.0, 100.0, 12000, 1.2e9, 0, ""),
        ("20240304", "600519", 110.0, 110.0, 110.0, 110.0, 100.0, 900, 9.9e7, 0, ""),
        ("20240305", "600519", 112.0, 118.0, 110.0, 115.0, 110.0, 30000, 3.4e9, 0, ""),
        ("20240306", "600519", 115.0, 116.0, 112.0, 113.0, 115.0, 26000, 2.9e9, 0, ""),
        ("20240307", "600519", 113.0, 114.0, 111.0, 112.0, 113.0, 21000, 2.4e9, 0, ""),
        ("20240308", "600519", 112.0, 113.0, 110.0, 111.0, 112.0, 19000, 2.1e9, 0, ""),
        # 300750 chinext 20%: limit up that opened intraday (low < limit_up).
        ("20240301", "300750", 198.0, 202.0, 196.0, 200.0, 200.0, 50000, 1.0e9, 0, ""),
        ("20240304", "300750", 205.0, 208.0, 199.0, 200.0, 200.0, 48000, 9.6e8, 0, ""),
        ("20240305", "300750", 230.0, 240.0, 225.0, 240.0, 200.0, 90000, 2.1e9, 0, ""),
        ("20240306", "300750", 238.0, 244.0, 232.0, 236.0, 240.0, 70000, 1.7e9, 0, ""),
        ("20240307", "300750", 236.0, 240.0, 230.0, 234.0, 236.0, 60000, 1.4e9, 0, ""),
        ("20240308", "300750", 234.0, 238.0, 229.0, 232.0, 234.0, 55000, 1.3e9, 0, ""),
        # 000001 sz_main 10%: normal, then a suspension day (volume = 0).
        ("20240301", "000001", 9.9, 10.1, 9.8, 10.0, 10.0, 800000, 8.0e8, 0, ""),
        ("20240304", "000001", 10.0, 10.3, 9.9, 10.2, 10.0, 900000, 9.2e8, 0, ""),
        ("20240305", "000001", 10.2, 10.4, 10.1, 10.3, 10.2, 850000, 8.8e8, 0, ""),
        ("20240306", "000001", 0.0, 0.0, 0.0, 10.3, 10.3, 0, 0.0, 0, ""),
        ("20240307", "000001", 10.3, 10.5, 10.2, 10.4, 10.3, 810000, 8.4e8, 0, ""),
        ("20240308", "000001", 10.4, 10.6, 10.3, 10.5, 10.4, 790000, 8.3e8, 0, ""),
        # 688981 star 20%: locked limit down -- an exit that cannot be executed.
        ("20240301", "688981", 49.0, 51.0, 48.5, 50.0, 50.0, 40000, 2.0e9, 0, ""),
        ("20240304", "688981", 50.0, 52.0, 49.5, 51.0, 50.0, 42000, 2.1e9, 0, ""),
        ("20240305", "688981", 51.0, 52.0, 50.0, 50.0, 51.0, 39000, 1.9e9, 0, ""),
        ("20240306", "688981", 50.0, 51.0, 49.0, 50.0, 50.0, 38000, 1.9e9, 0, ""),
        ("20240307", "688981", 40.0, 40.0, 40.0, 40.0, 50.0, 5000, 2.0e8, 0, ""),
        ("20240308", "688981", 41.0, 43.0, 40.0, 42.0, 40.0, 33000, 1.4e9, 0, ""),
        # 600123 sh_main + ST -> 5% band, thin turnover.
        ("20240301", "600123", 7.9, 8.1, 7.8, 8.0, 8.0, 30000, 2.4e7, 1, ""),
        ("20240304", "600123", 8.0, 8.2, 7.9, 8.1, 8.0, 28000, 2.3e7, 1, ""),
        ("20240305", "600123", 8.1, 8.2, 8.0, 8.1, 8.1, 26000, 2.1e7, 1, ""),
        ("20240306", "600123", 8.1, 8.3, 8.0, 8.2, 8.1, 25000, 2.0e7, 1, ""),
        ("20240307", "600123", 8.2, 8.3, 8.1, 8.2, 8.2, 24000, 2.0e7, 1, ""),
        ("20240308", "600123", 8.2, 8.3, 8.1, 8.2, 8.2, 23000, 1.9e7, 1, ""),
        # 301999 chinext, listed 20240305 -> inside the new-listing window.
        ("20240305", "301999", 30.0, 60.0, 29.0, 55.0, 20.0, 200000, 1.1e10, 0, "20240305"),
        ("20240306", "301999", 56.0, 58.0, 50.0, 52.0, 55.0, 150000, 7.8e9, 0, "20240305"),
        ("20240307", "301999", 52.0, 54.0, 48.0, 50.0, 52.0, 120000, 6.0e9, 0, "20240305"),
        ("20240308", "301999", 50.0, 52.0, 47.0, 48.0, 50.0, 100000, 4.8e9, 0, "20240305"),
    ]
    return pd.DataFrame(rows, columns=[
        "date", "symbol", "open", "high", "low", "close", "pre_close",
        "volume", "amount", "is_st", "list_date",
    ])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build an A-share tradability-state panel from daily bars."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bars", help="daily bar CSV/parquet (unadjusted prices)")
    source.add_argument("--demo", action="store_true", help="use the built-in fixture")
    parser.add_argument("--out", help="write the panel here; default stdout CSV")
    parser.add_argument("--limit-open-fill", type=float, default=0.3,
                        help="fill ratio on a limit bar that opened intraday (default 0.3)")
    parser.add_argument("--new-listing-days", type=int, default=5,
                        help="trading days after listing treated as unreliable (default 5)")
    parser.add_argument("--min-amount", type=float, default=0.0,
                        help="turnover below this marks low_liquidity (default 0 = off)")
    parser.add_argument("--price-eps", type=float, default=0.005,
                        help="absolute price tolerance when comparing to limits")
    parser.add_argument("--allow-adjusted", action="store_true",
                        help="accept adjusted prices; every limit verdict becomes a proxy")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        bars = demo_bars() if args.demo else load_bars(args.bars, args.allow_adjusted)
        if args.demo:
            bars = load_bars_frame(bars)
        panel = build_panel(
            bars,
            limit_open_fill=args.limit_open_fill,
            new_listing_days=args.new_listing_days,
            min_amount=args.min_amount,
            price_eps=args.price_eps,
        )
    except (ValueError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.out:
        if args.out.endswith(".parquet"):
            panel.to_parquet(args.out, index=False)
        else:
            panel.to_csv(args.out, index=False)
        blocked = int(((panel["buy_capacity"] < 1) | (panel["sell_capacity"] < 1)).sum())
        print("wrote %d rows to %s (%d constrained bars)" % (len(panel), args.out, blocked))
    else:
        print(panel.to_csv(index=False))
    return 0


def load_bars_frame(df):
    """Normalize an in-memory bar frame with the same rules as load_bars."""
    import pandas as pd
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    df["symbol"] = df["symbol"].astype(str)
    df["date"] = df["date"].astype(str).str.replace("-", "", regex=False)
    df["is_st"] = df["is_st"].map(_truthy) if "is_st" in df.columns else False
    if "list_date" in df.columns:
        df["list_date"] = df["list_date"].astype(str)
    for col in ("open", "high", "low", "close", "volume", "pre_close", "amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values(["symbol", "date"], kind="mergesort").reset_index(drop=True)


if __name__ == "__main__":
    raise SystemExit(main())
