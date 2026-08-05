"""End-to-end smoke test over the real CLI and real files.

The unittest suite exercises the functions in-process; this exercises the actual
subprocess + CSV/JSON round trip that a user hits, so a broken CLI wiring or a
serialization bug cannot pass silently.

    python validation/smoke.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run(script, *args):
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *args],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    bars = tmp / "bars.csv"
    panel = tmp / "panel.csv"
    trades = tmp / "trades.csv"
    report = tmp / "audit.json"
    ledger = tmp / "ledger.csv"

    # 1) demo fixtures out to disk, exactly as a user would stage them
    sys.path.insert(0, str(SCRIPTS))
    import audit_trades as auditor
    import build_tradability_panel as builder

    builder.demo_bars().to_csv(bars, index=False)
    auditor.demo_trades().to_csv(trades, index=False)

    # 2) build the state panel through the CLI
    run("build_tradability_panel.py", "--bars", str(bars),
        "--min-amount", "5e7", "--out", str(panel))
    assert panel.exists(), "panel was not written"

    # 3) audit through the CLI, reading the panel back from disk
    run("audit_trades.py", "--trades", str(trades), "--panel", str(panel),
        "--out", str(report), "--ledger", str(ledger))
    data = json.loads(report.read_text(encoding="utf-8"))

    # 4) contract gate
    run("validate_audit.py", str(report))

    # 5) the audit must actually find the planted defects
    assert data["status"] == "fail", data["status"]
    fired = {f["id"] for f in data["findings"]}
    for rule in ("C1_price_outside_limit", "C2_t1_violation",
                 "C3_short_without_inventory", "H1_buy_on_locked_limit_up",
                 "H2_sell_on_locked_limit_down", "H3_trade_on_halted_day",
                 "M1_buy_on_limit_up_open", "M3_new_listing_window",
                 "M4_participation_breach"):
        assert rule in fired, "rule %s did not fire through the CLI" % rule

    assert data["metrics"]["state_coverage"] == 1.0
    assert data["metrics"]["phantom_alpha_share"] > 0.5
    assert ledger.exists() and ledger.read_text(encoding="utf-8").count("\n") > 10

    # 6) symbols must survive the CSV round trip with their leading zeros
    assert any(f["evidence"] and f["evidence"][0]["symbol"] == "000001"
               for f in data["findings"] if f["id"] == "C2_t1_violation")

    # 7) adjusted prices must be refused at the door
    adjusted = tmp / "adjusted.csv"
    frame = builder.demo_bars()
    frame["price_type"] = "post"
    frame.to_csv(adjusted, index=False)
    rejected = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / "build_tradability_panel.py"),
         "--bars", str(adjusted), "--out", str(tmp / "x.csv")],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert rejected.returncode != 0, "adjusted prices were accepted"
    assert "adjusted" in rejected.stderr

print("a-share-tradability-auditor smoke: PASS")
