# Validation

Run from the repository root:

```bash
python scripts/build_tradability_panel.py --demo --min-amount 5e7 --out panel.csv
python scripts/audit_trades.py --demo --out audit.json --ledger ledger.csv
python scripts/validate_audit.py audit.json
python validation/smoke.py
python -B -m unittest discover -s tests -v
node scripts/validate-qsh-form.mjs SKILL.md      # optional, needs Node
```

## What the suite covers

**42 unit tests** across board inference (all five venues incl. `.SH`/`.BJ` suffixes and
non-A-share codes), exchange `ROUND_HALF_UP` limit pricing, date-resolved bands (the
main-board ST band on both sides of the 2026-07-06 change, and that the change leaves
ChiNext/STAR/BSE and non-ST names untouched), all six market states, suspension
inference from missing bars, the new-listing window
(including "absent `list_date` must never infer a new listing" and "a listing-day pop must
not be reported as a limit up"), adjusted-price rejection, duplicate-bar rejection, T+1 vs
naked-short classification and their split on an oversized sell, price-band violations,
the participation cap, `fill_ratio = min(...)` rather than a product, phantom-alpha
attribution, status precedence, evidence indices pointing at original input rows,
parameter-domain rejection, Chinese side labels, the output-contract validator's five
rejection paths, and byte-level determinism of the whole demo pipeline.

**`validation/smoke.py`** runs the real CLI over real files end to end: bars CSV → panel CSV
→ audit JSON + ledger CSV → contract gate. It asserts nine planted defects actually fire
through the subprocess boundary, that leading-zero symbols survive the CSV round trip, and
that an adjusted-price panel is refused with a non-zero exit.

## What it does not cover

Pandadata integration is not exercised here — `fetch_bar_panel.py` is a documented interface,
not a live call, so no credentials or vendor responses are embedded. The analysis layer is
offline and deterministic by design and was tested against the fixture only.

`limit_open_fill` and `participation` are **scenario assumptions, not calibrated fill
models**. The daily-bar state machine is an optimistic upper bound: it distinguishes a
sealed one-word limit from an intraday-opened one, but not queue position, seal strength or
how long the board stayed open. Any claim about real fill probability needs minute or
tick data.

`runnable` is a community self-validation level, not official verification.
