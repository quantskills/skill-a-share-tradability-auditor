#!/usr/bin/env python3
"""Assemble the unadjusted A-share daily bar panel that the auditor needs.

This is the ONE place that depends on Pandadata. `build_tradability_panel.py`
and `audit_trades.py` are pure pandas and run fully offline on any bar panel,
so the audit never requires credentials -- this script only spares you the
manual export.

Contract: load the sibling `pandadata-api` skill, read its `references/
method-index.md`, then confirm each method's exact signature, fields, units and
date format in `api-docs.md` before calling. Do NOT guess parameters. Method
routing for this panel is specified in references/data-map.md.

HARD REQUIREMENT: request **unadjusted** (raw) prices. Price limits are computed
from `pre_close x (1 +/- pct)`; on a back-adjusted series that arithmetic is
wrong and every limit verdict downstream becomes silently wrong. The emitted
panel carries `price_type` so the builder can refuse adjusted input.

Usage (once pandadata-api is available):
    python fetch_bar_panel.py --symbols 600519 000001 --start 20240101 \
        --end 20241231 --out bars.csv
    python fetch_bar_panel.py --universe universe.csv --start 20240101 \
        --end 20241231 --out bars.parquet
"""
from __future__ import annotations

import argparse

# Panel columns consumed by build_tradability_panel.py.
# Required -> the builder errors without them. Optional -> the builder degrades
# gracefully and records the degradation in `note` / `limit_reliable`.
PANEL_COLUMNS = [
    # required, from get_stock_daily (unadjusted)
    "date", "symbol", "open", "high", "low", "close", "volume",
    # strongly recommended
    "pre_close",    # exchange 前收盘价; without it the builder falls back to
                    # the previous observed close, which is wrong across
                    # ex-dividend dates and therefore mis-prices the band
    "amount",       # turnover in currency; drives the participation cap
    # optional but materially improves the audit
    "limit_up",     # exchange-published band; preferred over inference
    "limit_down",
    "is_st",        # risk-warning flag -> 5% band on the main boards
    "list_date",    # IPO date -> new-listing window
    "price_type",   # must be "raw"
]


def fetch(symbols, start, end):
    """Return a tidy unadjusted bar panel. Requires the pandadata-api skill.

    Implementation outline (fill in against pandadata-api's confirmed contracts;
    see references/data-map.md for which field comes from which method):

        1. Resolve the universe. Use a point-in-time membership list if you have
           one -- pulling today's list and applying it to history reintroduces
           survivorship bias that `skill-survivorship-universe-auditor` exists
           to catch.
        2. get_stock_daily(symbol, start, end) with adjustment disabled ->
           date/open/high/low/close/volume/amount/pre_close.
           Smoke-test ONE symbol over ONE week first: check shape, column names,
           date format, units (shares vs lots, yuan vs wan-yuan) and why empty
           results are empty, before widening the query.
        3. Security status (ST / risk-warning flag, listing date, and any
           published daily limit prices): confirm the exact method and field
           names via pandadata-api's method-index -- do NOT assume a name here.
           If the account cannot serve them, emit the panel WITHOUT those columns
           and let the builder degrade; do not fabricate values.
        4. Suspension needs no dedicated feed: the builder infers it from bars
           missing on a date the rest of the universe traded. That inference is
           only as good as the universe breadth, so keep the universe wide.
        5. Concatenate, set price_type="raw", and write. Keep raw units; record
           the method names, parameters, query time and latest data date in the
           final report.

    Missing optional columns are allowed. Missing REQUIRED columns are not --
    return them or fail loudly.
    """
    raise SystemExit(
        "fetch_bar_panel.py is a documented interface, not a stub to guess at.\n"
        "Wire it to the sibling skill-pandadata-api (read its method-index.md and\n"
        "api-docs.md first), or export an unadjusted bar panel yourself with the\n"
        "columns listed in PANEL_COLUMNS and feed it straight to\n"
        "build_tradability_panel.py --bars <file>."
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch an unadjusted A-share daily bar panel from Pandadata."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--symbols", nargs="+", help="explicit symbol list")
    source.add_argument("--universe", help="CSV with a `symbol` column")
    parser.add_argument("--start", required=True, help="YYYYMMDD")
    parser.add_argument("--end", required=True, help="YYYYMMDD")
    parser.add_argument("--out", required=True, help="output CSV/parquet path")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    symbols = args.symbols
    if args.universe:
        import pandas as pd
        symbols = pd.read_csv(args.universe, dtype=str)["symbol"].tolist()
    panel = fetch(symbols, args.start, args.end)
    if args.out.endswith(".parquet"):
        panel.to_parquet(args.out, index=False)
    else:
        panel.to_csv(args.out, index=False)
    print("wrote %d rows to %s" % (len(panel), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
