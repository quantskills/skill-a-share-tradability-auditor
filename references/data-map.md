# Data Map — 输入面板字段路由

## 接入结论

- 模式：**混合接入**。分析层（`build_tradability_panel` / `audit_trades` /
  `validate_audit`）纯 pandas、离线、确定性；只有 `fetch_bar_panel.py` 依赖 Pandadata。
- 覆盖范围：Pandadata 提供未复权日线与成交额；ST 状态、上市日期、交易所公布的涨跌停价
  需按其 method-index 确认可得性。
- 必须由用户提供：**交易流**（策略回测输出或券商成交回报）。这是私有数据，任何数据源
  都不会替你生成。

## 面板列 → 来源

| 面板列 | 必需性 | 来源 | 规范化规则 |
|---|---|---|---|
| `date, symbol, open, high, low, close, volume` | **必需** | `get_stock_daily`（未复权） | `date` 归一为 `YYYYMMDD`；`symbol` 保留 6 位补零字符串 |
| `pre_close` | 强烈建议 | `get_stock_daily` | 交易所前收盘价。缺失时回退"上一根 bar 收盘"，**除权日会错**，脚本在 `note` 登记 |
| `amount` | 强烈建议 | `get_stock_daily` | 成交额，参与率上限的分母；确认单位是元还是万元 |
| `limit_up`, `limit_down` | 可选（优先） | 交易所公布值 | 有则直接用，优于按板块推断 |
| `is_st` | 可选 | 证券状态/风险警示标识 | 主板 ST → 5% 带宽；创业板/科创板/北交所仍为原带宽 |
| `list_date` | 可选 | 证券基础信息 | 驱动新股窗口；**缺失时不推断次新**，避免全池误标 |
| `price_type` | 建议 | 由取数侧写入 | 必须为 `raw`；非 raw 时 builder 拒绝运行 |
| 停牌 | 无需字段 | 由面板自身推断 | 全市场有成交而本标的无 bar，且落在该标的首末 bar 之间 → `halted` |
| **交易流** | **必需** | 用户 / 回测引擎 / 券商 | `date, symbol, side` + `notional` 或 `shares`；建议附 `price`、`forward_return` |

## 调用原则

1. 用户已提供符合契约的 bars CSV 时直接用，不重复取数。
2. 需要取数且落在覆盖范围内时，加载兄弟 Skill `pandadata-api`：先读
   `references/method-index.md`，再读目标方法在 `api-docs.md` 中的完整参数与响应字段，
   **不凭记忆编造参数名**。
3. 先做单标的、单周 smoke test，检查 `shape`、列名、日期格式、单位（股/手、元/万元）
   与空结果原因，再扩大区间与股票池。
4. **务必关闭复权**。复权价会让涨跌停推断整体失效，且失效方式是静默的。
5. 结果落成新的规范化 CSV，再交给 builder；不要在原始 DataFrame 上就地覆盖。
6. 最终报告登记：方法名、参数、查询时间、最新数据日期、原始行数、标准化行数、字段映射。

## 股票池的点位一致性

用**当期成分**而不是今天的名单去拉历史，否则会重新引入幸存者偏差——那正是
`skill-survivorship-universe-auditor` 要解决的问题。本 Skill 不做成分审计，
但停牌推断的质量直接依赖股票池宽度：池子越窄，"全市场有成交"这个判据越不可靠。

## 失败与降级

- SDK / 凭证 / 服务未配置：明确返回 `insufficient-evidence` 并列出缺少的配置，
  **不回退到伪造数据**。
- 空结果：先查交易日、日期格式、代码格式（是否需要 `.SH`/`.SZ`/`.BJ` 后缀）、
  接口窗口与必要筛选条件。
- 只覆盖部分字段：保留已取到的证据，向用户索取缺失的私有字段；builder 会按列存在性
  降级并在 `note` / `limit_reliable` 登记，不整体报错。
- 所有代理变量必须写入 `assumptions` 与 `limitations`，不得把推断的涨跌停价描述为
  交易所公布值。
