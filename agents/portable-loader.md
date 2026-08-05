# Portable Loader Prompt

Use this prompt in agents that do not natively discover `SKILL.md` folders.

```text
You have access to a local skill named a-share-tradability-auditor at:
<A_SHARE_TRADABILITY_AUDITOR_SKILL_ROOT>

When the user request matches this skill's SKILL.md description:
1. Read <A_SHARE_TRADABILITY_AUDITOR_SKILL_ROOT>/SKILL.md.
2. Follow the workflow and guardrails in that file exactly.
3. Load referenced files under <A_SHARE_TRADABILITY_AUDITOR_SKILL_ROOT>/references/
   only when needed: limit-rules.md before touching any price-limit or board logic,
   methodology.md before changing thresholds or interpretation, output-schema.md
   before emitting a report, data-map.md before fetching data, boundary.md before
   routing the user to a different skill.
4. Run bundled scripts from the skill root only after reading the relevant instructions.
5. Preserve the documented input contract, board and price-limit rules, state
   definitions, the four-layer fill model, output contract, validation limits and
   risk boundaries.
6. Require UNADJUSTED prices. Never compute price limits on an adjusted series, and
   never silently pass --allow-adjusted on the user's behalf.
7. Do not invent trade logs, bar data, interfaces, credentials, limit prices, fill
   ratios or audit results. The trade log is private data that must come from the
   user, their backtest engine or their broker.
8. Do not run a backtest, place orders or modify source data. This skill only audits
   and writes its output to new files. It gives no investment advice.
```
