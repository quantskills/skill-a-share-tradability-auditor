# 判重边界 — 与 QuantSkills 现有能力的分工

组织内已有 4 个 `*-auditor` 与多个回测/风险类 Skill。本文逐个说明**为什么本 Skill
不与它们重复**，供 registry 判重与用户路由使用。

## 一句话定位

> 现有的 auditor 审的是**数据本身对不对**；本 Skill 审的是**这些数据上的成交假设成不成立**。
> 前者问"价格对不对"，后者问"这个价格你拿不拿得到"。

## 逐个对照

| 现有 Skill | 它审什么 | 本 Skill 审什么 | 是否重叠 |
|---|---|---|---|
| `skill-corporate-action-adjustment-auditor` | 拆股/分红在原始价与复权价之间是否一致 | 复权价**根本不能用来推断涨跌停**——本 Skill 直接拒绝复权输入 | 否。它保证复权正确；本 Skill 要求未复权 |
| `skill-survivorship-universe-auditor` | 成分是否点位一致、退市收益是否缺失 | 给定股票池后，**池内每根 bar 能不能交易** | 否。它定义域，本 Skill 定义域内的可成交性 |
| `skill-intraday-data-quality-auditor` | 分钟 OHLCV 的时间戳、缺口、价量缺陷 | 日频制度约束（涨跌停/停牌/T+1/参与率） | 否。数据完整性 vs 制度可行性 |
| `skill-futures-roll-auditor` | 期货连续合约换月与调整台账 | A 股现货的账户与价格限制 | 否。资产类别与约束类型均不同 |
| `skill-numerical-leak-check` | 因子/模型是否用到未来数据 | 不含未来数据的信号，**能不能真的成交** | 否。互补：先无泄漏，再可成交 |
| `skill-backtest` / `skill-factor-backtest` | 跑回测、出净值 | 对已有回测**输出**做事后审计，指出净值虚高多少 | 否。被审计对象 vs 审计器 |
| `skill-backtest-overfit` | 多重检验与过拟合（DSR / PBO / purged CV） | 制度约束导致的**不可成交收益** | 否。统计有效性 vs 执行可行性。两者是回测可信度的两条正交轴 |
| `skill-portfolio-liquidity-stress-test` | 组合层清算容量、赎回缺口、冲击成本（**事前情景**） | 逐笔交易在**历史某一日**是否可成交（**事后审计**） | 否。组合/前瞻 vs 逐笔/回溯。参与率概念相通但对象不同 |
| `skill-b6-limitup-pool` | 每日维护涨停池、首板连板、题材与情绪面 | 涨停作为**成交阻断条件**，用于扣减策略收益 | 否。它把涨停当研究对象；本 Skill 把涨停当约束条件 |
| `skill-b11-auto-stop-loss-take-profit` / `skill-b12-intraday-position-manager` | 实盘仓位与止盈止损**执行** | 不下单、不管仓，只出审计报告 | 否。执行 vs 审计 |
| `skill-risk-model` / `skill-portfolio-checkup` | 风险暴露与组合体检 | 成交可行性，不算风险暴露 | 否 |

## 硬性边界（写死在实现里）

1. **不跑回测、不生成净值曲线**——被审计的回测由 `skill-backtest` 系列产出。
2. **不下单、不改原始数据**——所有输出写到新文件。
3. **不给投资建议**——输出是缺陷清单与收益归因，不是买卖意见。
4. **不做数据修复**——发现复权/缺列问题时报错并指向对应的 auditor，不自行改数。
5. **拒绝复权价输入**（除非显式 `--allow-adjusted`，且此时全部判定降级为 proxy）。

## 推荐串联顺序

```
survivorship-universe-auditor   定义点位一致的股票池
        ↓
corporate-action-adjustment-auditor   保证复权口径正确（并保留一份未复权价）
        ↓
numerical-leak-check            保证信号不含未来数据
        ↓
backtest / factor-backtest      产出交易流与净值
        ↓
【本 Skill】a-share-tradability-auditor   扣掉不可成交的幽灵收益
        ↓
backtest-overfit                在可成交收益上再做多重检验折扣
        ↓
portfolio-liquidity-stress-test 上线前的组合级容量与赎回压力
```

顺序的意义：**先把假收益扣掉，再做统计显著性检验**。在含幽灵收益的曲线上算 Deflated
Sharpe，扣的是错的分子。

## 反向 handoff

- 用户要"修复"而非"审计" → 交回 `skill-backtest` 系列改撮合逻辑。
- 用户要研究涨停板本身（连板、情绪、题材） → 交 `skill-b6-limitup-pool`。
- 用户要组合层容量与赎回 → 交 `skill-portfolio-liquidity-stress-test`。
- 用户要期货/可转债/ETF 的对应约束 → 本 Skill 不覆盖，明确说明而不是硬套 A 股股票规则。
