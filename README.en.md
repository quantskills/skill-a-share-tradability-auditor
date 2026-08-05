# A-Share Tradability Auditor

[简体中文](README.md) | **English**

> How much of your backtest equity curve is PnL the market would never have given you?

Replays a backtest or signal trade log against A-share execution reality and splits
headline PnL into **executable PnL** and **phantom PnL**, with per-trade evidence.

## Why

In A-shares the largest source of inflated backtests is not overfitting — it is an
impossible fill assumption. Momentum and event signals naturally fire on **sealed
limit-up days**, where the backtest fills at the close and a live account gets nothing.
Stop-losses fire on limit-down days, where the backtest exits cleanly and a live account
stays trapped. Add rebalancing through suspensions, same-day round trips that violate
T+1 settlement, sells exceeding inventory (a naked short that A-share cash accounts
cannot do), and orders larger than the whole day's turnover.

These biases are **systematically positive**, and most backtest engines do not check for
any of them.

## Quick start

```bash
pip install pandas numpy

# fully offline, no data or credentials required
python scripts/build_tradability_panel.py --demo --min-amount 5e7 --out panel.csv
python scripts/audit_trades.py --demo --out audit.json --ledger ledger.csv
python scripts/validate_audit.py audit.json

# real data
python scripts/build_tradability_panel.py --bars bars.csv --out panel.csv
python scripts/audit_trades.py --trades trades.csv --panel panel.csv --out audit.json
```

## Pipeline

| Script | Role | Needs Pandadata |
| --- | --- | --- |
| `fetch_bar_panel.py` | pull an **unadjusted** daily bar panel | yes (documented interface) |
| `build_tradability_panel.py` | bars → date × symbol tradability-state panel | no |
| `audit_trades.py` | replay the trade log, attribute PnL | no |
| `validate_audit.py` | gate the report against the output contract | no |

Only the fetch step touches Pandadata. Bring your own bar panel and the whole audit runs
offline and deterministically.

## What it checks

| Layer | Check | Severity |
| --- | --- | --- |
| Inventory / settlement | T+1 violation (same-day round trip) | critical |
| | Naked short (sell exceeds holdings) | critical |
| Price validity | Fill price outside the day's limit band | critical |
| Market state | Buy on a sealed limit up / sell on a sealed limit down / trade on a suspension | high |
| | Limit bar that opened intraday (partial) / new-listing window | medium |
| Capacity | Order above the participation cap on daily turnover | medium |
| Universe | ST names / thin turnover | low |

Bands: main boards ±10%, ChiNext and STAR ±20%, BSE ±30%.

**Bands are resolved per bar date, not pinned to today's rulebook.** Main-board
risk-warning names (ST/*ST) carried a ±5% band from 1998 until **2026-07-06**, when the
revised SSE/SZSE 《交易规则》 (published 2026-04-24) raised it to ±10% to match ordinary
main-board stocks. ChiNext, STAR and BSE never halved their band under risk warning.
Auditing a 2024 sample under the 2026 band would silently widen every ST bar and hide
real limit blocks; the reverse would invent blocks that never happened — hence the
effective-dated `BOARD_RULE_HISTORY` table. Add future changes there, not at the call site.

`fill_ratio = min(inventory_ratio, market_ratio, participation_ratio)` — a minimum, not a
product, because the three layers are not independent and multiplying understates fills.

## Key metrics

| Metric | How to read it |
| --- | --- |
| `limit_up_entry_rate` | **The single most important number.** > 5% → defer signals one day and re-run; > 20% → the strategy is largely eating unobtainable limit-up returns |
| `limit_down_exit_trap_rate` | The stop-loss killer: the backtest exits at the limit-down price, the live account stays trapped |
| `phantom_alpha_share` | Share of headline PnL that is not executable; above `--fail-decay` (0.30) the audit fails |
| `fill_weighted_ratio` | Notional-weighted fill rate; below 0.8 the assumptions are detached from the market |
| `t1_violation_count`, `short_without_inventory_count`, `price_outside_limit_count` | Non-zero means a **matching-engine defect** — fix the engine before discussing returns |

Status is one of `pass` / `warning` / `fail` / `insufficient-evidence`. `critical` and
`high` findings must carry locatable evidence (symbol + date + values) or
`validate_audit.py` rejects the report.

## Hard constraints

- **Adjusted prices are refused.** Limits are `pre_close × (1 ± pct)`; on an adjusted
  series that arithmetic is wrong and fails silently. `--allow-adjusted` exists but
  downgrades every limit verdict to a proxy.
- **Audit only** — no backtesting, no order placement, no mutation of source data. All
  output goes to new files.
- **Assumptions stay assumptions.** `limit_open_fill` and `participation` are scenario
  parameters, not fill guarantees; run a sensitivity analysis.
- **Coverage first.** Below `--min-coverage` the report returns `insufficient-evidence`;
  "found no problems" and "was unable to look" must never be conflated.
- The trade log is private data. No data source will generate it for you.
- The audit replays from a **flat** starting inventory. Strategies with an opening
  position must prepend opening buys, or the first sell is misreported as a naked short.

## Boundary vs existing QuantSkills auditors

The four existing auditors check whether the **data** is right. This one checks whether
the **execution assumption on that data** is possible.

`corporate-action-adjustment` (adjustment consistency), `survivorship-universe`
(point-in-time membership), `intraday-data-quality` (minute-bar defects) and
`futures-roll` (contract rolls) all guard data correctness. `backtest-overfit` guards
statistical validity. This skill guards **execution feasibility** — the second axis,
orthogonal to overfitting. `b6-limitup-pool` studies limit-ups as a research subject;
this skill treats them as a constraint.

Recommended chain: `survivorship` → `corporate-action` → `numerical-leak-check` →
`backtest` → **this skill** → `backtest-overfit` → `portfolio-liquidity-stress-test`.
Strip the phantom PnL *before* testing significance — a Deflated Sharpe computed on a
curve containing phantom alpha is discounting the wrong numerator.

Full comparison in [`references/boundary.md`](references/boundary.md).

## Validation and limits

See [validation/README.md](validation/README.md). 42 unit tests plus a full-CLI
end-to-end smoke test.

The daily-bar state machine is an **optimistic upper bound**: it separates a sealed
one-word limit from an intraday-opened one, but not queue position, seal strength or how
long the board stayed open. Real fill probability needs minute or tick data.

Nothing here is investment advice, and `pass` only means the executed checks found
nothing — not that the strategy works.

GPL-3.0-only.
