# Read-only Data Progress

[Documentation index](README.md) · [Data safety](operations.md)

Choose the intended checkout before querying. Production status comes from the
primary checkout, not a worktree's partial snapshot. A missing worktree database
is expected and must not trigger a download, rebuild or copy over production.

Run this PowerShell block from the intended checkout. It uses the installed
repository's `config.DATA_DIR`, opens SQLite with `mode=ro`, and holds one read
transaction for the queries. A missing database fails without creating a file.
Do not call a fetcher's schema initializer merely to inspect an older database.

```powershell
@'
import sqlite3
from contextlib import closing
from config import config

path = config.DATA_DIR / "official_daily.sqlite"
print(f"Database: {path}")
with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
    conn.execute("BEGIN")
    coverage = conn.execute(
        "SELECT market, MIN(date), MAX(date), COUNT(DISTINCT date), "
        "COUNT(DISTINCT code) FROM official_daily_price GROUP BY market"
    ).fetchall()
    print("Coverage (market, first, latest, dates, codes):", coverage)
    dates_by_market = {}
    for market, *_ in coverage:
        dates_by_market[market] = {row[0] for row in conn.execute(
            "SELECT DISTINCT date FROM official_daily_price "
            "WHERE market=? ORDER BY date", (market,)
        )}
    observed_days = set().union(*dates_by_market.values())
    for market, dates in dates_by_market.items():
        first, last = min(dates), max(dates)
        peer_only = sorted(day for day in observed_days - dates if first <= day <= last)
        print("Full observed interval:", market, first, last, "dates:", len(dates))
        print("Potential gaps observed in another market:", market, peer_only)
    columns = {row[1] for row in conn.execute(
        "PRAGMA table_info(official_history_raw_ranges)"
    )}
    period = "period" if "period" in columns else "year_month"
    if period not in columns:
        print("History checkpoints unavailable; no schema changes made.")
    else:
        # The identifier is chosen only from the two known schema versions.
        print("Checkpoint coverage (market, unit, status, count, first, last):",
              conn.execute(
                  "SELECT market, CASE WHEN code = '' THEN 'market-day' "
                  "ELSE 'symbol-month' END AS unit, status, "
                  f"COUNT(*), MIN({period}), MAX({period}) "
                  "FROM official_history_raw_ranges GROUP BY market, unit, status"
              ).fetchall())
        print("Unsettled checkpoints:", conn.execute(
            f"SELECT market, code, {period}, status FROM official_history_raw_ranges "
            f"WHERE status NOT IN ('ok', 'empty') ORDER BY market, code, {period}"
        ).fetchall())
'@ | uv run python -
```

Report the latest date and checkpoint state per market in Traditional Chinese.
The current checkpoint column is `period`; older snapshots used `year_month`.
TWSE units include symbol-months (`code <> ''`) and whole-market days
(`code = ''`); TPEx history units are whole-market days. The query groups those
units separately so month and day bounds cannot become one apparent range.

The comparison uses every observed date, including gaps older than the latest
ten trading sessions. A date present in another market but absent inside this
market's covered interval is a candidate gap, not proof that both markets traded
that day. Confirm candidates against an authoritative trading calendar through a
read-only lookup before calling them missing trading days. Dates absent in both
markets, or outside a market's observed bounds, cannot be detected by this
comparison; do not claim gap-free coverage without independent calendar evidence.
Name unsettled market/code/period records rather than
reporting only their count. Rows alone do not prove historical re-fetch completion:
bulk and historical feeds have different quantity definitions, so checkpoints
must be read separately. Do not repair or migrate anything during this check.
