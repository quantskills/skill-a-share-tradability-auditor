# 🚦 A-Share Tradability Auditor

**简体中文** | [English](README.en.md)

> 你的回测净值里，有多少收益是市场根本不会给你的？

![type](https://img.shields.io/badge/type-agent--skill-blue)
![category](https://img.shields.io/badge/category-auditor-orange)
![license](https://img.shields.io/badge/license-GPLv3-blue)
![validation](https://img.shields.io/badge/validation-runnable-green)

---

## 📖 这是什么

A 股回测虚高最大的来源**不是过拟合，而是成交假设不成立**。

动量与事件类信号天然倾向在**封死涨停日**发出买入——回测按收盘价全额成交，实盘一股买不到；
止损策略在**跌停日**卖出——回测干净出清，实盘被闷在里面继续跌；还有停牌日照常调仓、
当日买当日卖违反 T+1、卖出量超过持仓等于裸卖空、单笔委托超过全天成交额。

这些偏差**系统性偏正**，而绝大多数回测框架默认不检查。

本 Skill 把交易流放回历史行情逐笔重放，输出一个数：**`phantom_alpha_share`** ——
账面收益里不可成交的那部分占比，并把每一笔问题交易定位到 `symbol + date + 具体数值`。

它**只审计**：不跑回测、不下单、不改数、不给投资建议。

## 🚀 快速开始

```bash
pip install pandas numpy

cp -r skill-a-share-tradability-auditor ~/.claude/skills/a-share-tradability-auditor
```

零依赖离线跑通（无需任何数据与凭证）：

```bash
python scripts/build_tradability_panel.py --demo --min-amount 5e7 --out panel.csv
python scripts/audit_trades.py --demo --out audit.json --ledger ledger.csv
python scripts/validate_audit.py audit.json
```

触发示例：

```text
审计一下我这份 2023 年动量策略的回测交易流，看有多少收益是涨停买不进造成的
我的止损策略回测很好看，实盘差很多，帮我查是不是跌停卖不出的问题
检查这份交易记录有没有违反 T+1 或者裸卖空
```

## 📊 输出长什么样

```json
{
  "status": "fail",
  "metrics": {
    "limit_up_entry_rate": 0.222,
    "limit_down_exit_trap_rate": 0.333,
    "fill_weighted_ratio": 0.112,
    "t1_violation_count": 1,
    "phantom_alpha_share": 0.906
  },
  "findings": [
    {
      "id": "H1_buy_on_locked_limit_up",
      "severity": "high",
      "count": 1,
      "impact": "在封死的涨停板上买入——买单排不进队列",
      "recommended_fix": "把封死涨停日的买入信号顺延到下一可交易日，并按次日开盘价重估收益",
      "evidence": [{"trade_index": 4, "symbol": "600519", "date": "20240304",
                    "state": "limit_up_locked"}]
    }
  ]
}
```

`limit_up_entry_rate` 是**最重要的单一指标**：> 5% 就该把信号顺延一日重跑；
> 20% 基本可以判定策略在吃不可得的涨停收益。

## 🔍 审计什么

| 层 | 检查 | 严重度 |
|---|---|---|
| 库存 / 结算 | 违反 T+1（当日买当日卖） | critical |
| | 裸卖空（卖出超过持仓） | critical |
| 报价有效性 | 成交价越过当日涨跌停价 | critical |
| 市场状态 | 封死涨停买入 / 封死跌停卖出 / 停牌日交易 | high |
| | 开板涨跌停部分成交 / 新股上市窗口 | medium |
| 容量 | 委托超过当日成交额的参与率上限 | medium |
| 域 | ST 标的 / 低流动性 | low |

板块带宽：沪深主板 ±10%、创业板/科创板 ±20%、北交所 ±30%。

**带宽按 bar 日期解析，不是全样本一刀切**：沪深主板风险警示股（ST/*ST）自 1998 年起为
±5%，**自 2026-07-06 起调整为 ±10%**（沪深交易所修订后的《交易规则》，2026-04-24 发布），
与主板普通股一致；创业板/科创板/北交所的 ST **从来就是原带宽**。
用 2026 年的带宽审 2024 年的样本会静默放宽每一根 ST bar、掩盖真实封板阻断，反之则凭空
造出不存在的阻断——所以规则做成了带生效日的沿革表（`BOARD_RULE_HISTORY`），
新增变更改表即可。详见 [`references/limit-rules.md`](references/limit-rules.md#11-规则沿革主板风险警示股带宽)。

## 📦 目录结构

```text
skill-a-share-tradability-auditor/
├── SKILL.md
├── USAGE.md
├── scripts/
│   ├── fetch_bar_panel.py           # 路径A：Pandadata 取未复权日线（唯一依赖点）
│   ├── build_tradability_panel.py   # 核心1：行情 → 可交易性状态面板
│   ├── audit_trades.py              # 核心2：交易流重放 → 审计报告 + 收益归因
│   └── validate_audit.py            # 输出契约校验（可作流水线闸门）
├── references/
│   ├── limit-rules.md               # 涨跌停/停牌/T+1/次新 制度规则表
│   ├── methodology.md               # 四层约束模型与指标解读
│   ├── output-schema.md             # 输出契约与规则字典
│   ├── data-map.md                  # 字段来源路由
│   └── boundary.md                  # 与现有 auditor 的判重边界
├── tests/ · validation/ · agents/
```

## 📐 核心约束

| 约束 | 说明 |
| --- | --- |
| 🚫 拒绝复权价 | 涨跌停价必须用未复权价推断；见到 `price_type` 非 raw 直接报错 |
| 🔒 只审计不修改 | 不跑回测、不下单、不改原始数据，输出一律写新文件 |
| 📏 假设即假设 | `limit_open_fill` / `participation` 是情景参数，不是撮合保证，必须做敏感性分析 |
| 🕳️ 证据强制 | critical/high 必须附 `symbol + date + 数值`，否则契约校验直接判违规 |
| ❓ 覆盖率优先 | 面板覆盖率不足时返回 `insufficient-evidence`，不得输出定量结论 |
| 🚫 只述不荐 | 输出缺陷清单与收益归因，不构成任何投资建议 |

## 🧭 与现有 auditor 的分工

> 现有 4 个 auditor 审的是**数据本身对不对**；本 Skill 审的是**这些数据上的成交假设成不成立**。

`corporate-action-adjustment`（复权一致性）、`survivorship-universe`（成分点位）、
`intraday-data-quality`（分钟数据缺陷）、`futures-roll`（期货换月）都在保证数据正确；
`backtest-overfit` 管统计有效性；本 Skill 管**执行可行性**——与过拟合正交的第二条轴。
`b6-limitup-pool` 把涨停当研究对象，本 Skill 把涨停当约束条件。

推荐串联：`survivorship` → `corporate-action` → `numerical-leak-check` → `backtest`
→ **本 Skill** → `backtest-overfit` → `portfolio-liquidity-stress-test`。
**先扣掉假收益，再做统计显著性检验**——在含幽灵收益的曲线上算 Deflated Sharpe，扣的是错的分子。

完整对照见 [`references/boundary.md`](references/boundary.md)。

## ✅ 验证

42 个单元测试 + 全 CLI 端到端 smoke，覆盖板块推断、交易所四舍五入（含 2026-07-06
ST 带宽变更的按日期解析）、六种状态、停牌推断、T+1 与裸卖空的区分、收益归因、
状态优先级与逐字节确定性。
详见 [validation/README.md](validation/README.md)。

日线判定是**乐观上界**：能区分一字封死与盘中开板，但区分不了封单强度、开板时长与排队位置。
真实成交概率需要分钟线/逐笔数据。

## ⚠️ 免责声明

本仓库仅作研究方法层面的整理，不验证任何收益声明，不构成任何投资建议。
`pass` 只表示已执行的检查未发现问题，不代表策略有效或未来盈利。

## 📜 License

Copyright (C) 2026 the QuantSkills contributors.

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE).

## 🐼 PandaAI / QUANTSKILLS 社群

<div align="center">
  <img src="https://raw.githubusercontent.com/quantskills/.github/main/profile/assets/pandaai-community-qr.jpg" alt="PandaAI 社群二维码" width="220">
  <br>
  <sub>扫码加入 PandaAI 社群，交流 QUANTSKILLS 技能、Agent 工作流与量化研究实践。</sub>
</div>
