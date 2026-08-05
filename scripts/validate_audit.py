#!/usr/bin/env python3
"""Validate an audit report against the output contract before it is consumed.

Guards the two ways this skill could quietly mislead:
  - a report that claims `pass` while carrying unresolved findings or thin
    panel coverage,
  - a report that asserts `critical` / `high` severity without locatable
    evidence rows.

Exits non-zero on hard failures so it can gate a pipeline.

Usage:
    python validate_audit.py audit.json
    python validate_audit.py audit.json --min-coverage 0.95
"""
from __future__ import annotations

import argparse
import json
import sys

REQUIRED_TOP = [
    "status", "input_summary", "assumptions", "metrics",
    "findings", "limitations", "next_actions",
]
VALID_STATUS = {"pass", "fail", "warning", "insufficient-evidence"}
VALID_SEVERITY = {"critical", "high", "medium", "low", "info"}
EVIDENCE_REQUIRED = {"critical", "high"}
REQUIRED_METRICS = [
    "trades_total", "state_coverage", "blocked_trade_count",
    "fill_weighted_ratio", "limit_up_entry_rate",
    "limit_down_exit_trap_rate", "t1_violation_count",
]


def validate(report, min_coverage=0.90):
    errors = []
    warnings = []

    missing = [k for k in REQUIRED_TOP if k not in report]
    if missing:
        return ["missing required top-level keys: %s" % missing], []

    status = report["status"]
    if status not in VALID_STATUS:
        errors.append("status %r is not one of %s" % (status, sorted(VALID_STATUS)))

    missing_metrics = [m for m in REQUIRED_METRICS if m not in report.get("metrics", {})]
    if missing_metrics:
        errors.append("metrics missing: %s" % missing_metrics)

    findings = report.get("findings") or []
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        findings = []

    severities = set()
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            errors.append("findings[%d] must be an object" % i)
            continue
        for key in ("id", "severity", "count", "impact", "recommended_fix"):
            if key not in f:
                errors.append("findings[%d] missing %s" % (i, key))
        sev = f.get("severity")
        if sev not in VALID_SEVERITY:
            errors.append("findings[%d].severity %r invalid" % (i, sev))
            continue
        severities.add(sev)
        if sev in EVIDENCE_REQUIRED and not f.get("evidence"):
            errors.append(
                "findings[%d] (%s) claims %s without locatable evidence"
                % (i, f.get("id"), sev)
            )

    coverage = report.get("metrics", {}).get("state_coverage")
    if isinstance(coverage, (int, float)):
        if coverage < min_coverage and status != "insufficient-evidence":
            errors.append(
                "state_coverage %.4f is below %.4f but status is %r, not "
                "insufficient-evidence" % (coverage, min_coverage, status)
            )
    else:
        errors.append("metrics.state_coverage is missing or not numeric")

    if status == "pass" and severities:
        errors.append("status=pass but findings carry severities %s" % sorted(severities))
    if "critical" in severities and status not in {"fail", "insufficient-evidence"}:
        errors.append("critical findings present but status is %r" % status)

    attribution = report.get("return_attribution")
    if attribution is None:
        warnings.append(
            "no return_attribution: the trade log had no forward_return column, "
            "so phantom alpha was not quantified"
        )
    else:
        share = attribution.get("phantom_alpha_share")
        if isinstance(share, (int, float)) and share > 0.5 and status == "warning":
            warnings.append(
                "phantom_alpha_share=%.2f is very high for a `warning`; consider "
                "lowering --fail-decay" % share
            )

    if not report.get("limitations"):
        errors.append("limitations must not be empty")
    if not report.get("next_actions"):
        errors.append("next_actions must not be empty")

    return errors, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate a tradability audit report.")
    parser.add_argument("report", help="audit JSON produced by audit_trades.py")
    parser.add_argument("--min-coverage", type=float, default=0.90)
    args = parser.parse_args(argv)

    try:
        with open(args.report, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError) as exc:
        print("error: cannot read report: %s" % exc, file=sys.stderr)
        return 2

    errors, warnings = validate(report, args.min_coverage)
    for w in warnings:
        print("warning: %s" % w)
    for e in errors:
        print("error: %s" % e, file=sys.stderr)
    if errors:
        print("FAILED: %d contract violation(s)" % len(errors), file=sys.stderr)
        return 1
    print("audit report contract: PASS (status=%s)" % report["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
