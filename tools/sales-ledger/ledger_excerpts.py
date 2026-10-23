"""Excerpts from a sales-ledger parser that reads order confirmations and
marketplace statements from email into one ledger.

The full parser (about 8,000 lines: per-channel email formats, plan-name
normalization, revenue splits, geography, PDF attachments) stays private.
These are three self-contained pieces that show how it is checked:

  * plain helpers for the dates and money that arrive in a dozen formats
  * a detector for unlabeled sitewide sales, found from price patterns alone
  * a regression check that compares every release against a frozen baseline

Names of clients, orders and customers have been removed. Run the tests with
    python3 -m unittest test_ledger_excerpts -v
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def to_money(s):
    """'$1,295.50' -> 1295.5. Anything unparseable -> None, never 0, so a
    missing price can never pass as a free order."""
    if s is None:
        return None
    s = str(s).replace(",", "").replace("$", "").strip()
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def parse_date_any(s):
    """Best-effort date string -> YYYY-MM-DD. Channels write dates as
    '3rd September 2020', '09/03/20', '2020-09-03' and more."""
    if not s:
        return ""
    s = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", s.strip(), flags=re.IGNORECASE)
    for fmt in ("%m.%d.%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
                "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
                "%d %B %Y", "%d %b %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def clean_order_id(raw):
    """'#1001' and 1001.0 (from a spreadsheet) both -> '1001'."""
    if raw is None:
        return ""
    s = str(raw).strip().lstrip("#").strip()
    return re.sub(r"\.0$", "", s)


# --------------------------------------------------------------------------
# Unlabeled sitewide sales
# --------------------------------------------------------------------------
# One marketplace runs sitewide sales without saying so in its order emails:
# the order simply arrives at a lower price. Prices there always land on a
# 25-cent figure, markdown or not, so the cents cannot tell the two apart.
#
# What can: for one plan in one file format, a price that is an exact
# 5-percent fraction of that same plan and format's highest observed price is
# unlikely to be coincidence. A list price can also rise over years of
# history, so one isolated match is only SUSPECTED. Sales on that marketplace
# run a weekend or a week, so when a DIFFERENT plan shows the same discount
# within ten days, that is one real sale hitting several plans: CONFIRMED.
#
# This only adds a flag. It never changes a price, because the price in the
# ledger is already the one the email states.
MARKDOWN_PCTS = (0.95, 0.90, 0.85, 0.80, 0.75, 0.70)
MARKDOWN_TOLERANCE = 0.0005
CLUSTER_WINDOW_DAYS = 10


def detect_sitewide_markdowns(rows, platform):
    """Append SITEWIDE_SALE_SUSPECTED_* or SITEWIDE_SALE_CONFIRMED_* to the
    _flags list of each standard order on `platform` that fits the pattern."""
    groups = defaultdict(list)
    for r in rows:
        if r["platform"] != platform or r["order_type"] != "STANDARD":
            continue
        if any(f.startswith(("ADDON", "DISCOUNT_CODE")) for f in r.get("_flags", [])):
            continue                      # price already explained; don't double-flag
        price = r.get("base_plan_price")
        if not price or price <= 0:
            continue
        groups[(r["plan_name"], r["deliverable_format"])].append(r)

    candidates = []                       # (row, pct, date, key)
    for key, grp in groups.items():
        top = max(r["base_plan_price"] for r in grp)
        for r in grp:
            if r["base_plan_price"] == top:
                continue
            ratio = r["base_plan_price"] / top
            for pct in MARKDOWN_PCTS:
                if abs(ratio - pct) < MARKDOWN_TOLERANCE:
                    try:
                        d = datetime.strptime(r["date"], "%Y-%m-%d")
                    except (ValueError, TypeError):
                        d = None
                    candidates.append((r, pct, d, key))
                    break

    for r, pct, d, key in candidates:
        confirmed = d is not None and any(
            r2 is not r and key2 != key and d2 is not None and pct2 == pct
            and abs((d - d2).days) <= CLUSTER_WINDOW_DAYS
            for r2, pct2, d2, key2 in candidates)
        off = int(round((1 - pct) * 100))
        r.setdefault("_flags", []).append(
            f"SITEWIDE_SALE_CONFIRMED_{off}PCT_OFF_MULTI_PLAN_CLUSTER" if confirmed
            else f"SITEWIDE_SALE_SUSPECTED_{off}PCT_OFF_VERIFY")


# --------------------------------------------------------------------------
# Regression against a frozen baseline
# --------------------------------------------------------------------------
# Every release is compared row by row with a frozen, hand-checked baseline
# before its output is trusted. This is the check that caught the parser
# dropping the base price from bundled orders, with a comment in the source
# asserting the opposite.
#
# The baseline was hand-entered and is not consistent about whether an
# add-on or a discount is folded into its one price column; real orders were
# found both ways. So every price the new version produces is a candidate,
# and only a gap that NONE of them closes is reported.
PRICE_TOLERANCE = 5.00


def regression_warnings(orders, baseline_rows, normalize=lambda name: name):
    """Return human-readable warnings for every order whose price or plan
    disagrees with the baseline. `baseline_rows` are dicts as read from the
    baseline CSV; column names are matched loosely."""
    if not baseline_rows:
        return []
    cols = {c.lower(): c for c in baseline_rows[0]}

    def col(*names):
        return next((cols[n] for n in names if n in cols), None)

    c_platform = col("platform")
    c_order = col("order_id", "orderid", "order #", "order_number")
    c_plan = col("plan_name", "plan")
    c_price = col("base_plan_price", "price", "total_price", "total")

    index = {}
    for b in baseline_rows:
        oid = clean_order_id(b.get(c_order, "")) if c_order else ""
        if oid:
            index[((b.get(c_platform) or "").strip(), oid)] = b

    warnings = []
    for row in orders:
        oid, plat = row.get("order_id", ""), row.get("platform", "")
        match = index.get((plat, oid)) or next(
            (b for (_, o), b in index.items() if o == oid), None)   # platform names drift
        if not match:
            continue

        candidates = [v for v in (row.get("total_price"), row.get("base_plan_price"),
                                  row.get("_predisc_price")) if v is not None]
        base_price = to_money(match.get(c_price, "")) if c_price else None
        if candidates and base_price is not None:
            closest = min(candidates, key=lambda v: abs(v - base_price))
            if abs(closest - base_price) > PRICE_TOLERANCE:
                warnings.append(f"{plat} #{oid}: PRICE MISMATCH "
                                f"(baseline ${base_price:.2f} vs now ${closest:.2f})")

        base_plan = (match.get(c_plan, "") or "").strip() if c_plan else ""
        if base_plan and row.get("plan_name") and \
                normalize(base_plan).lower() != row["plan_name"].lower():
            warnings.append(f"{plat} #{oid}: PLAN MISMATCH "
                            f"(baseline '{base_plan}' vs now '{row['plan_name']}')")
    return warnings
