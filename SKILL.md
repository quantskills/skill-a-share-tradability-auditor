---
name: a-share-tradability-auditor
description: >-
  A-share tradability-constraint auditor — replays a backtest or signal trade log
  against date-resolved price limits (10%/20%/30%; main-board ST was 5% until the
  2026-07-06 rule change), locked vs intraday-opened limit
  bars, suspensions, T+1 settlement, the naked-short ban, the new-listing window
  and turnover participation caps, then splits headline PnL into executable PnL
  and phantom PnL. Use when the user asks 回测收益是不是真的能成交、涨停买不进、
  跌停卖不出、停牌怎么处理、T+1 约束、一字板、可交易性/可成交性审计, or wants to know
  why a live account underperforms its backtest. Fills the ecosystem gap: the four
  existing auditors all check whether the DATA is right, none checks whether the
  EXECUTION ASSUMPTION is possible.
license: GPL-3.0-only
metadata:
  organization: QuantSkills
  organization_url: https://github.com/quantskills
  repository: skill-a-share-tradability-auditor
  repository_url: https://github.com/quantskills/skill-a-share-tradability-auditor
  project_type: skill
  collection: backtest-integrity
  creator: 13817660341-coder
  creator_url: https://github.com/13817660341-coder
  maintainer: 13817660341-coder
  maintainer_url: https://github.com/13817660341-coder
quantSkills:
  project_type: skill
  category: auditor
  tags:
    - a-share
    - tradability
    - price-limit
    - t-plus-1
    - suspension
    - backtest-integrity
    - execution
    - pandadata
  platforms:
    - claude-code
    - codex
    - hermes
    - openclaw
    - cursor
  language: zh-en
  # 全新仓库，尚未在真实回测流上被第三方使用过；跑通实战后再升 stable。
  status: draft
  validation_level: runnable
  maintainer_type: community
  # Pandadata is required only by fetch_bar_panel.py. build_tradability_panel /
  # audit_trades / validate_audit are pure pandas — feed your own unadjusted bar
  # panel and the whole audit runs with no Pandadata at all.
  requires:
    - skill-pandadata-api
  summary_zh: >-
    A 股可交易性约束审计：把回测交易流放回历史行情，逐笔判定涨跌停封板、停牌、T+1、
    裸卖空、新股窗口与参与率上限，把账面收益拆成"可成交收益"与"幽灵收益"，
    并定位到具体交易。回答"这条净值曲线里有多少是市场根本不会给你的"。
  summary_en: >-
    A-share tradability auditor that replays a trade log against price limits,
    suspensions, T+1 settlement, the naked-short ban and participation caps,
    splitting headline PnL into executable and phantom PnL with per-trade evidence.
---

```json qsh-form
{
  "version": 1,
  "task": {
    "placeholder": "例如：审计我这份 2023 年动量策略的回测交易流，看有多少收益是涨停买不进造成的",
    "required": true
  },
  "fields": [
    {
      "key": "participation",
      "type": "number",
      "label": "单笔可参与成交额上限（0-1）"
    },
    {
      "key": "limit_open_fill",
      "type": "number",
      "label": "开板涨跌停日的成交比例假设（0-1）"
    },
    {
      "key": "fail_decay",
      "type": "number",
      "label": "幽灵收益占比超过多少判 fail（0-1）"
    },
    {
      "key": "new_listing_days",
      "type": "number",
      "label": "新股上市后视为不可靠的交易日数"
    }
  ],
  "prompt_template": "请处理任务：{{task}}。参与率上限：{{participation}}；开板成交比例：{{limit_open_fill}}；幽灵收益判负阈值：{{fail_decay}}；新股窗口天数：{{new_listing_days}}。附件：{{#attachments}}"
}
```

# A-Share Tradability Auditor

把输入的回测结果当作**需要验证的证据**，而不是默认可信的结论。先冻结口径，
再运行确定性检查，最后把"已证实的问题"和"缺失证据"分开报告。

> 定位边界：本 Skill **只审计、不回测、不下单、不改数**。产出是缺陷清单 + 收益归因，
> 不构成任何投资建议。

---

## 它解决什么

A 股回测虚高最大的来源不是过拟合，而是**成交假设不成立**：

- 动量/事件类信号天然倾向在**封死涨停日**发出买入 —— 回测按收盘价全额成交，实盘一股买不到；
- 止损策略在**跌停日**卖出 —— 回测干净出清，实盘被闷在里面继续跌；
- **停牌日**照常调仓；
- 当日买当日卖，**违反 T+1**；
- 卖出量超过持仓，等于**裸卖空**（A 股普通账户做不到）；
- 单笔委托超过全天成交额。

这些偏差**系统性偏正**，且绝大多数回测框架默认不检查。本 Skill 把它们量化成一个数：
`phantom_alpha_share` —— 净值曲线里市场根本不会给你的那部分。

---

## 与现有 auditor 的分工（registry 判重关键）

> 现有 4 个 auditor 审的是**数据本身对不对**；本 Skill 审的是**这些数据上的成交假设成不成立**。

| | 现有 auditor | 本 Skill |
|---|---|---|
| `corporate-action-adjustment` | 复权是否一致 | 复权价**不能用来推断涨跌停**，本 Skill 拒绝复权输入 |
| `survivorship-universe` | 成分点位是否一致 | 池内每根 bar **能不能交易** |
| `intraday-data-quality` | 分钟数据缺陷 | 日频**制度约束** |
| `futures-roll` | 期货换月 | A 股现货账户与价格限制 |
| `backtest-overfit` | 统计有效性（DSR/PBO） | **执行可行性** —— 与过拟合正交的第二条轴 |
| `portfolio-liquidity-stress-test` | 组合层、前瞻情景 | 逐笔、回溯到历史某一日 |
| `b6-limitup-pool` | 把涨停当**研究对象** | 把涨停当**约束条件** |

完整对照与推荐串联顺序见 `references/boundary.md`。核心原则：
**先扣掉假收益，再做统计显著性检验**——在含幽灵收益的曲线上算 Deflated Sharpe，扣的是错的分子。

---

## 核心工作流

1. **准备未复权日线面板**。用户自备 → 直接用；需取数 → `fetch_bar_panel.py`
   走 `skill-pandadata-api`（接口路由见 `references/data-map.md`）。
   **务必关闭复权**——复权价会让涨跌停推断静默失效。
2. **构建可交易性状态面板**：`build_tradability_panel.py` 推断板块、涨跌幅带宽、
   涨跌停价（`ROUND_HALF_UP` 到 0.01），判定 6 种状态与买/卖容量，标记 ST、
   新股窗口、低流动性、停牌。制度规则见 `references/limit-rules.md`。
3. **重放交易流**：`audit_trades.py` 按四层约束逐笔判定成交比例
   （库存/T+1 → 市场状态 → 报价有效性 → 参与率，取 `min`）。
4. **收益归因**：交易流带 `forward_return` 时，输出 `phantom_alpha_share`。
5. **校验输出契约**：`validate_audit.py` 拦截"无证据的高严重度"与"覆盖率不足却判 pass"。
6. **顺延重估**：按发现的约束修回测（封板买入顺延、跌停卖出顺延、停牌不交易、超参与率拆单），
   重跑后 `phantom_alpha_share` 应显著下降。方法见 `references/methodology.md`。

```bash
# 1) 离线烟雾测试（无需任何数据与凭证）
python scripts/build_tradability_panel.py --demo --out panel.csv
python scripts/audit_trades.py --demo --out audit.json --ledger ledger.csv

# 2) 真实数据
python scripts/fetch_bar_panel.py --universe universe.csv --start 20230101 --end 20241231 --out bars.csv
python scripts/build_tradability_panel.py --bars bars.csv --min-amount 5e7 --out panel.csv
python scripts/audit_trades.py --trades trades.csv --panel panel.csv --out audit.json --ledger ledger.csv
python scripts/validate_audit.py audit.json
```

---

## 输入契约

**行情面板**（`--bars`，未复权）：必需 `date, symbol, open, high, low, close, volume`；
强烈建议 `pre_close`（缺失时回退上一根收盘，除权日会错）、`amount`（参与率分母）；
可选 `limit_up/limit_down`（交易所公布值优于推断）、`is_st`、`list_date`、`price_type`。

**交易流**（`--trades`）：必需 `date, symbol, side`（buy/sell，接受中英文与 1/-1），
以及 `notional` 或 `shares` 至少其一；建议附 `price`（用于检出越过涨跌停的成交）
与 `forward_return`（**没有它就只有成交率，没有收益归因**）。

字段名不一致时先显式建立映射，不要猜测。缺关键字段时停止定量结论，列出补数清单。

---

## 运行参数

`build_tradability_panel.py`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--bars` / `--demo` | 二选一必填 | 输入源 |
| `--limit-open-fill` | `0.3` | 开板涨跌停日的成交比例假设，`[0,1]` |
| `--new-listing-days` | `5` | 新股窗口交易日数；**仅在提供 `list_date` 时生效** |
| `--min-amount` | `0`（关闭） | 成交额低于此值标记 `low_liquidity` |
| `--price-eps` | `0.005` | 与涨跌停价比较的绝对容差（元） |
| `--allow-adjusted` | 关 | 强行接受复权价；此时所有涨跌停判定降级为 proxy |

`audit_trades.py`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--trades` / `--demo` | 二选一必填 | 输入源 |
| `--panel` | 非 demo 必填 | 上一步的状态面板 |
| `--participation` | `0.1` | 单笔可占当日成交额的上限，`(0,1]` |
| `--fail-decay` | `0.30` | `phantom_alpha_share` 超过即判 `fail`，`[0,1]` |
| `--min-coverage` | `0.90` | 面板覆盖率低于此值返回 `insufficient-evidence` |
| `--ledger` / `--out` / `--no-ledger-in-json` | — | 逐笔台账 CSV / JSON 报告 / 摘除台账 |

---

## 输出契约

机器可读 JSON + 简洁中文结论。完整定义见 `references/output-schema.md`。
关键指标：

| 指标 | 怎么读 |
|---|---|
| `limit_up_entry_rate` | **最重要的单一指标**。> 5% 就该把信号顺延一日重跑；> 20% 基本可判定策略在吃不可得的涨停收益 |
| `limit_down_exit_trap_rate` | 止损策略的致命伤：回测按跌停价出清，实盘被套 |
| `phantom_alpha_share` | 净值虚高幅度，> `--fail-decay` 判 `fail` |
| `fill_weighted_ratio` | 金额加权成交率，< 0.8 说明假设与市场脱节 |
| `t1_violation_count` / `short_without_inventory_count` / `price_outside_limit_count` | 非零即为**引擎缺陷**，先修引擎再谈收益 |

状态：`pass` / `warning` / `fail` / `insufficient-evidence`。
`critical` 与 `high` 必须附可定位证据（symbol + date + 数值），否则 `validate_audit.py` 判合约违规。

---

## 方法与证据

修改阈值、公式或解释前先读 `references/methodology.md` 与 `references/limit-rules.md`。
保留数据版本、时区、样本窗、参数取值与所有降级项（`note` / `limit_reliable` / `limitations`），
使另一位研究员可以复现。同一份 bars + trades + 参数 → 逐字节相同的报告。

**日线判定是乐观上界**：日线能区分"一字封死"与"盘中开板"，但区分不了封单强度、
开板时长与排队位置。真实成交概率需要分钟线/逐笔数据。

---

## 数据源策略

- 用户已提供规范面板时直接用，不重复调用外部数据源。
- 需取数且落在覆盖范围内时，先读 `references/data-map.md`，再用兄弟 Skill
  `pandadata-api`：先读其 `method-index.md` 与 `api-docs.md` 确认签名与字段，**不凭记忆编造参数**。
- 分析脚本保持离线、确定性；Pandadata 只负责取数，字段标准化后再交给脚本。
- **交易流是私有数据**，任何数据源都不会替你生成；必须由用户、回测引擎或券商提供。
- SDK/凭证未配置时明确返回 `insufficient-evidence` 并列出缺少的配置，**不回退到伪造数据**。

---

## 使用边界

- 只用于量化研究、数据质量与执行可行性分析，**不构成投资建议**。
- 不把缺失证据写成"通过"；覆盖率不足时"没发现问题"与"没能力发现问题"必须区分。
- 不把启发式假设（`limit_open_fill`、`participation`）描述为撮合保证；必须做敏感性分析。
- 不自动下单、不修改原始数据；所有输出写到新文件。
- 不覆盖：融券、大宗交易、协议转让、盘后固定价格交易、ETF 申赎，以及可转债/期货的对应规则。
- 审计从**零期初持仓**重放；期初有持仓的策略须补期初买入行，否则首笔卖出会被误报为裸卖空。

---

## 免责声明

本仓库仅作研究方法层面的整理，不验证任何收益声明，不构成任何投资建议。
`pass` 只表示已执行的检查未发现问题，不代表策略有效或未来盈利。
