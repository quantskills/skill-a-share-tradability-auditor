# 使用指南 · skill-a-share-tradability-auditor

把回测交易流放回历史行情逐笔重放，量化"不可成交的幽灵收益"。本文覆盖安装、两条数据路径、
管线命令、输入格式、常见场景、结果解读与顺延重估。

---

## 目录
1. [安装](#1-安装)
2. [它做什么 / 不做什么](#2-它做什么--不做什么)
3. [两条数据路径](#3-两条数据路径)
4. [管线三步](#4-管线三步)
5. [输入格式](#5-输入格式)
6. [常见场景](#6-常见场景)
7. [结果解读](#7-结果解读)
8. [顺延重估：审计之后做什么](#8-顺延重估审计之后做什么)
9. [下游对接](#9-下游对接)

---

## 1. 安装

```bash
pip install pandas numpy
# 可选：读写 parquet
pip install pyarrow
# 可选：校验 SKILL.md 的 qsh-form 表单声明
node scripts/validate-qsh-form.mjs SKILL.md
```

`build_tradability_panel` / `audit_trades` / `validate_audit` 纯 pandas，完全离线；
只有 `fetch_bar_panel` 依赖 Pandadata（`skill-pandadata-api`）。

## 2. 它做什么 / 不做什么

- **做**：把交易流 + 行情 → 逐笔可成交性判定 + 收益归因 + 缺陷清单（带证据）。
- **不做**：不跑回测（交 `skill-backtest`）、不下单（交 `skill-b11/b12`）、
  不修数据（交对应 auditor）、不给投资建议。

## 3. 两条数据路径

| 路径 | 依赖 | 说明 |
|---|---|---|
| **A. Pandadata**（`fetch_bar_panel.py`） | `skill-pandadata-api` + 账号 | 自动取未复权日线；ST/上市日/交易所涨跌停价按可得性降级 |
| **B. 自备行情**（跳过 fetch） | 无——任何行情源都行 | 只要有 `date,symbol,open,high,low,close,volume` 就能跑 |

> ⚠️ **必须未复权**。涨跌停价 = `前收盘 × (1 ± 幅度)`，在复权序列上这个算式是错的，
> 而且错得静默。builder 见到 `price_type` 非 raw 直接拒绝运行。

## 4. 管线三步

```bash
# (可选) 0) 取未复权日线面板
python scripts/fetch_bar_panel.py --universe universe.csv \
    --start 20230101 --end 20241231 --out bars.csv

# 1) 行情 → 可交易性状态面板
python scripts/build_tradability_panel.py --bars bars.csv \
    --min-amount 5e7 --new-listing-days 5 --out panel.csv

# 2) 交易流重放 → 审计报告 + 逐笔台账
python scripts/audit_trades.py --trades trades.csv --panel panel.csv \
    --participation 0.1 --out audit.json --ledger ledger.csv

# 3) 输出契约校验（可作 CI 闸门，违约非零退出）
python scripts/validate_audit.py audit.json
```

不带任何数据先跑通：把 `--bars`/`--trades` 换成 `--demo`。

## 5. 输入格式

### 行情面板（`--bars`）

| 列 | 必需性 | 说明 |
|---|---|---|
| `date, symbol, open, high, low, close, volume` | **必需** | `date` 支持 `YYYYMMDD` 或 `YYYY-MM-DD`；`symbol` 保留 6 位补零 |
| `pre_close` | 强烈建议 | 交易所前收盘价。缺失时回退"上一根收盘"，**除权日会错**，脚本在 `note` 登记 |
| `amount` | 强烈建议 | 成交额，参与率上限的分母 |
| `limit_up` / `limit_down` | 可选（优先） | 交易所公布值，优于按板块推断 |
| `is_st` | 可选 | 接受 `1/true/yes/ST/*ST` |
| `list_date` | 可选 | 驱动新股窗口；**缺失时不推断次新**，避免全池误标 |
| `price_type` | 建议 | 必须是 `raw` |

停牌**不需要字段**：全市场有成交而本标的无 bar，且落在该标的首末 bar 之间 → 判 `halted`。
因此**股票池越宽，停牌推断越准**。

### 交易流（`--trades`）

| 列 | 必需性 | 说明 |
|---|---|---|
| `date, symbol, side` | **必需** | side 接受 `buy/sell`、`买入/卖出`、`1/-1`、`long/short` |
| `notional` 或 `shares` | **至少其一** | 库存台账的计量单位；两者都有时用 `shares` |
| `price` | 建议 | 缺了就查不出"成交价越过涨跌停"这类引擎缺陷 |
| `forward_return` | 建议 | **缺了就只有成交率，没有收益归因**——`phantom_alpha_share` 为空 |

> 期初持仓：审计从零库存重放。有期初持仓的策略必须在交易流最前面补买入行，
> 否则首笔卖出会被误报为裸卖空。

## 6. 常见场景

**只想知道涨停买入占比**

```bash
python scripts/audit_trades.py --trades t.csv --panel p.csv --no-ledger-in-json \
  | python3 -c "import json,sys;m=json.load(sys.stdin)['metrics'];print(m['limit_up_entry_rate'])"
```

**参数敏感性分析**（必做——`limit_open_fill` 与 `participation` 是假设不是事实）

```bash
for f in 0.0 0.3 0.6; do
  python scripts/build_tradability_panel.py --bars bars.csv --limit-open-fill $f --out p_$f.csv
  python scripts/audit_trades.py --trades t.csv --panel p_$f.csv --out a_$f.json --no-ledger-in-json
done
```

`limit_open_fill=0` 是最保守情形（开板也不给成交），给出 `phantom_alpha_share` 的上界。

**定位具体问题交易**

```bash
python scripts/audit_trades.py --trades t.csv --panel p.csv --ledger ledger.csv --out a.json
# ledger.csv 逐笔带 fill_ratio 与 rules 列，按 rules 筛即可
```

**接入 CI**：`validate_audit.py` 违约非零退出，可直接当闸门。

## 7. 结果解读

| 指标 | 阈值 | 含义 |
|---|---|---|
| `limit_up_entry_rate` | > 5% 警惕，> 20% 危险 | **最重要的单一指标**。策略在吃不可得的涨停收益 |
| `limit_down_exit_trap_rate` | > 5% 警惕 | 止损策略的致命伤：回测按跌停出清，实盘被套 |
| `phantom_alpha_share` | > 0.30 判 fail | 净值虚高幅度 |
| `fill_weighted_ratio` | < 0.8 警惕 | 金额加权成交率 |
| `t1_violation_count` 等三个 count | **非零即引擎缺陷** | 先修引擎，再谈收益 |

状态优先级：`覆盖率不足 > critical > 衰减超阈值 > 有发现 > pass`。

- `pass` 只表示**已执行的检查**未发现问题，不代表策略有效。
- `insufficient-evidence` 时**不得**给出定量结论——此时"没发现问题"与"没能力发现问题"
  无法区分。

`C1/C2/C3` 三条 critical 是**回测引擎缺陷**，不是市场约束：撮合价越界、T+1、裸卖空
都是引擎该拦而没拦的。必须先修引擎重跑，否则后续所有收益归因都建立在错误撮合之上。

## 8. 顺延重估：审计之后做什么

审计告诉你"哪些收益是假的"，不告诉你"真实收益是多少"。要拿后者，按发现的约束重跑回测：

1. 封死涨停日的买入 → 顺延到下一个 `buy_capacity > 0` 的交易日，按该日开盘价成交。
2. 封死跌停日的卖出 → 顺延，持仓继续承担后续跌幅。
3. 停牌日 → 不产生交易；复牌日按开盘价重估，单独检查停牌期间跳空。
4. 超参与率的委托 → 拆到多日，叠加冲击成本。
5. 重跑后再审计一次：`phantom_alpha_share` 应显著下降；没降说明顺延逻辑没生效。

顺延本身会引入新偏差（顺延日价格已含当日信息），所以顺延后的曲线仍是上界，不是实盘承诺。

## 9. 下游对接

- 想在可成交收益上做统计检验 → `skill-backtest-overfit`（**顺序不能反**）
- 组合层容量与赎回压力 → `skill-portfolio-liquidity-stress-test`
- 研究涨停板本身（连板/情绪/题材） → `skill-b6-limitup-pool`
- 成分点位一致性 / 复权一致性 / 未来函数 → 对应的 auditor 与 `skill-numerical-leak-check`

完整判重边界与推荐串联顺序见 [`references/boundary.md`](references/boundary.md)。
