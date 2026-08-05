# 输出契约

`audit_trades.py` 的 JSON 报告结构。`validate_audit.py` 按本文校验，违约即非零退出。

## 顶层

```json
{
  "status": "pass | fail | warning | insufficient-evidence",
  "input_summary": {},
  "assumptions": {},
  "metrics": {},
  "return_attribution": {},
  "findings": [],
  "limitations": [],
  "next_actions": [],
  "ledger": []
}
```

`limitations` 与 `next_actions` 不得为空。`ledger` 可用 `--no-ledger-in-json` 摘除
（大交易流建议摘除并用 `--ledger` 单独落 CSV）。

## input_summary

| 字段 | 说明 |
|---|---|
| `trades` | 交易笔数 |
| `symbols` | 标的数 |
| `date_range` | `[起始日, 结束日]`，`YYYYMMDD` |
| `panel_rows` | 状态面板行数 |
| `size_unit` | `shares` 或 `notional`——库存台账的计量单位 |

## assumptions

必须完整回显，使报告自解释：`participation`、`fail_decay`、`min_coverage`、
`intraday_order`、`initial_inventory`。构建面板时的 `limit_open_fill`、
`new_listing_days`、`min_amount`、`price_eps` 记录在面板侧，报告引用时一并附上。

## metrics

必需字段（缺一即合约违规）：

`trades_total`、`state_coverage`、`blocked_trade_count`、`fill_weighted_ratio`、
`limit_up_entry_rate`、`limit_down_exit_trap_rate`、`t1_violation_count`

其余：`fully_blocked_trade_count`、`blocked_notional_share`、`halt_trade_rate`、
`short_without_inventory_count`、`price_outside_limit_count`、`phantom_alpha_share`。

## return_attribution

交易流带 `forward_return` 时产出，否则为 `null`（`validate_audit.py` 给 warning）：

```json
{
  "gross_signal_pnl": 0.0,
  "executable_pnl": 0.0,
  "phantom_pnl": 0.0,
  "phantom_alpha_share": 0.0,
  "note": "signed by side (buy=+, sell=-); notional-weighted forward return"
}
```

## findings[]

```json
{
  "id": "H1_buy_on_locked_limit_up",
  "severity": "critical | high | medium | low | info",
  "count": 12,
  "notional_share": 0.083,
  "impact": "一句话说明后果",
  "recommended_fix": "一句话说明怎么修",
  "evidence": [{"trade_index": 4, "symbol": "600519", "date": "20240304", "...": "..."}]
}
```

- `trade_index` 是**原始输入文件的行号**（0 起），不是内部重排后的序号。
- `severity ∈ {critical, high}` 必须携带非空 `evidence`。
- 按严重度、再按 count 降序排列。

## 规则字典

| id | 严重度 | 触发 |
|---|---|---|
| `C1_price_outside_limit` | critical | 成交价越过当日涨跌停价 |
| `C2_t1_violation` | critical | 卖出量超过已结算库存且当日有买入 |
| `C3_short_without_inventory` | critical | 卖出量超过全部持仓（裸卖空） |
| `H1_buy_on_locked_limit_up` | high | 在封死涨停上买入 |
| `H2_sell_on_locked_limit_down` | high | 在封死跌停上卖出 |
| `H3_trade_on_halted_day` | high | 停牌日交易 |
| `M1_buy_on_limit_up_open` | medium | 在开板涨停日买入（部分成交） |
| `M2_sell_on_limit_down_open` | medium | 在开板跌停日卖出（部分成交） |
| `M3_new_listing_window` | medium | 新股上市窗口内交易 |
| `M4_participation_breach` | medium | 委托金额超过当日成交额上限 |
| `L1_st_universe` | low | 交易 ST/*ST 标的 |
| `L2_low_liquidity` | low | 当日成交额低于阈值 |
| `E1_missing_state` | info | 该 (date, symbol) 在面板中缺失 |

`C1/C2/C3` 是**回测引擎缺陷**，不是市场约束——必须先修引擎再重跑，否则后续所有
收益归因都建立在错误撮合之上。

## ledger[]（逐笔台账）

`trade_row`、`date`、`symbol`、`side`、`size`、`state`、`inventory_ratio`、
`market_ratio`、`participation_ratio`、`fill_ratio`、`filled_size`、`rules[]`。

`--ledger out.csv` 落 CSV 时 `rules` 以 `|` 连接。台账是审计结论的可追溯层：
任何一条 finding 都应能在台账里定位到具体行。

## 状态判定优先级

```
state_coverage < min_coverage        → insufficient-evidence
存在 critical                        → fail
phantom_alpha_share > fail_decay     → fail
存在 high / medium / low             → warning
无发现                                → pass
```

`pass` 只表示**已执行的检查**未发现问题，不构成对策略有效性的任何判断。
