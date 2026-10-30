#!/usr/bin/env python3
"""
Percentages for the "Sales vs budget" screen on the office TV.

Run by the daily scheduled task, never by GitHub. It mirrors the maths on the
Eureka Quote Pipeline page so the TV and the page agree. It prints only
percentages and counts. No dollar figure ever leaves this script, because the
TV is a public screen.

Two steps:

  python3 sales_stats.py plan
      Prints the three invoice date ranges to look up in Xero with
      show_invoices_summary, as JSON: {"ranges": {id: [start, end], ...}}.

  python3 sales_stats.py compute QUOTES_DIR XERO_JSON [BUDGET]
      QUOTES_DIR  folder of quote JSON files saved from the pipeline page's
                  database (ArtifactData list, collection "quotes", out_dir).
      XERO_JSON   file holding {id: total_invoiced_inc_GST} for each range
                  from "plan", copied from Xero's AUD total_invoiced.
      BUDGET      annual budget ex GST. Defaults to 3,000,000, the same
                  default the pipeline page uses when none is saved.
      Prints the "sales" object to put on the Eureka TV data page.

Dates are Brisbane time (UTC+10, no daylight saving). Financial year starts
1 July. Xero summary totals include GST, so they are divided by 1.1. That is
within a fraction of a percent of the page, which uses each invoice's ex-GST
amount; GST-free sales make it very slightly high.
"""

import datetime as dt
import glob
import json
import os
import sys

BNE = dt.timezone(dt.timedelta(hours=10))
DEFAULT_BUDGET = 3_000_000
PROBS_DEFAULT = 50


def now_bne():
    override = os.environ.get("SALES_NOW")          # for testing only
    if override:
        return dt.datetime.fromisoformat(override).astimezone(BNE)
    return dt.datetime.now(BNE)


def fy_start(d):
    y = d.year if d.month >= 7 else d.year - 1
    return dt.datetime(y, 7, 1, tzinfo=BNE)


def month_start(y, m):
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return dt.datetime(y, m, 1, tzinfo=BNE)


def r3_from(now):
    # Date.UTC(y, m-3, d) in the page: the same day of the month three months back.
    y, m = now.year, now.month - 3
    while m < 1:
        m += 12
        y -= 1
    day = now.day
    # JavaScript rolls an impossible date (31 Feb) forward; match that.
    base = dt.datetime(y, m, 1, tzinfo=BNE)
    return base + dt.timedelta(days=day - 1)


def ranges(now):
    fy = fy_start(now)
    ms = month_start(now.year, now.month)
    today = now.date()
    out = {"month_to_date": [ms.date().isoformat(), today.isoformat()],
           "r3": [r3_from(now).date().isoformat(), today.isoformat()]}
    if ms > fy:
        out["fy_before_month"] = [fy.date().isoformat(), (ms - dt.timedelta(days=1)).date().isoformat()]
    return out


def load_quotes(folder):
    qs = []
    for f in glob.glob(os.path.join(folder, "**", "*.json"), recursive=True):
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        d = d.get("data", d) if isinstance(d, dict) else {}
        if "sent" in d:
            qs.append(d)
    return qs


def t_of(q):
    try:
        return dt.datetime.fromisoformat(str(q["sent"]).replace("Z", "+00:00")).astimezone(BNE)
    except Exception:
        return None


def status(q):
    return q.get("status") or "open"


def prob(q):
    st = status(q)
    if st == "won":
        return 100
    if st == "open":
        p = q.get("prob")
        return PROBS_DEFAULT if p is None else p
    return 0


def weighted(q):
    return (q.get("value") or 0) * prob(q) / 100


def won_not_invoiced(q):
    # Same test as the page: won, has a value, not marked "already invoiced",
    # and not matched to a Xero invoice (a manual link, or the automatic
    # match the page saves as autoInv when it loads Xero).
    return (status(q) == "won" and q.get("value")
            and q.get("invOverride") != "yes"
            and not q.get("invNo") and not q.get("autoInv"))


def compute(quotes, xero, budget):
    now = now_bne()
    fy = fy_start(now)
    y, m = now.year, now.month
    ms = month_start(y, m)
    mb = budget / 12

    def wni(a, b):
        return sum(q["value"] for q in quotes if won_not_invoiced(q) and t_of(q) and a <= t_of(q) < b)

    inc = lambda k: float(xero.get(k) or 0) / 1.1
    nowp = now + dt.timedelta(milliseconds=1)

    inv_before = inc("fy_before_month")
    inv_month = inc("month_to_date")
    inv_r3 = inc("r3")
    r3a = r3_from(now)

    before = inv_before + wni(fy, ms)
    cur = inv_month + wni(ms, nowp)
    won_y = before + cur

    # months gone in the FY, counting today as part of this month
    dim = (month_start(y, m + 1) - ms).days
    months_done = (y - fy.year) * 12 + (m - 7) + now.day / dim
    bud_ytd = mb * months_done

    fy_gone = months_done / 12 * 100
    fy_budget = won_y / budget * 100 if budget else 0
    sales_pct = won_y / bud_ytd * 100 if bud_ytd else 0

    # projection at the rolling 3-month rate
    rate = (inv_r3 + wni(r3a, nowp)) / 3
    left = 12 - ((y - fy.year) * 12 + (m - 7))
    fy_close = before + sum(max(cur, rate) if k == 0 else rate for k in range(left))
    proj_pct = fy_close / budget * 100 if budget else 0

    # weighted quotes: last three complete months of this FY
    prod = [q for q in quotes if t_of(q) and t_of(q) >= fy and status(q) not in ("excluded", "superseded")]
    rows = []
    k = 0
    while True:
        a = month_start(fy.year, 7 + k)
        if a > ms:
            break
        b = min(month_start(fy.year, 8 + k), nowp)
        rows.append({"v": sum(weighted(q) for q in prod if a <= t_of(q) < b), "cur": a == ms})
        k += 1
    done = [r for r in rows if not r["cur"]][-3:]
    avg = sum(r["v"] for r in done) / len(done) if done else 0
    quotes_pct = avg / mb * 100 if mb else 0

    # win rate this FY: won / produced (open, won, lost)
    won_n = sum(1 for q in prod if status(q) == "won")
    win_rate = round(won_n / len(prod) * 100) if prod else 0

    # quotes sent this month vs the same point last month
    sentable = [q for q in quotes if t_of(q) and status(q) not in ("excluded", "superseded")]
    pms = month_start(y, m - 1)
    elapsed = nowp - ms
    sent = sum(1 for q in sentable if ms <= t_of(q) < nowp)
    sent_prev = sum(1 for q in sentable if pms <= t_of(q) < min(pms + elapsed, ms))

    return {
        "asof": now.strftime("%A ") + str(now.day) + now.strftime(" %B %Y"),
        "fyGone": round(fy_gone, 1),
        "fyBudget": round(fy_budget, 1),
        "salesPct": round(sales_pct),
        "quotesPct": round(quotes_pct),
        "winRate": win_rate,
        "winOf": "%d won of %d quotes this year" % (won_n, len(prod)),
        "sent": sent,
        "sentPrev": sent_prev,
        "projPct": round(proj_pct),
    }


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "plan":
        print(json.dumps({"ranges": ranges(now_bne())}, indent=1))
        return
    if len(sys.argv) >= 4 and sys.argv[1] == "compute":
        quotes = load_quotes(sys.argv[2])
        if not quotes:
            sys.exit("No quotes found in %s" % sys.argv[2])
        with open(sys.argv[3], encoding="utf-8") as fh:
            xero = json.load(fh)
        need = set(ranges(now_bne()))
        missing = need - set(xero)
        if missing:
            sys.exit("Xero totals missing for: %s" % ", ".join(sorted(missing)))
        budget = float(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else DEFAULT_BUDGET
        print(json.dumps(compute(quotes, xero, budget), ensure_ascii=False))
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
