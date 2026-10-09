# Historical record: implementation status unverified

This retained proposal/design/task record is not a current behavior contract.
Original: [openspec/changes/repair-official-data-gaps/design.md](https://github.com/TaiwanCCyoyo/shioaji-stock-prices-legacy/blob/bcb73fd3804ff253fe87017da3b843dc606c858d/openspec/changes/repair-official-data-gaps/design.md).
No production action or acquisition is authorized by this record.

## Context

The measured MI_INDEX price-table signature covers 2010 and current dates and agrees with stored STOCK_DAY OHLC and traded quantities. Additional era probes are required before crawling. The primary checkout owns production data; development remains on a branch from refreshed origin/main. Other work owns parquet feature generation.

## Goals / Non-Goals

Goals: repair TWSE day gaps promptly, recover historical instruments using whole-market observations, retain existing row provenance, and keep interrupted downloads resumable. Non-goals match the proposal: no pre-2010 prices, changed parquet universe, research runs, schedule changes, broker work, or overwriting existing price rows with MI_INDEX.

## Decisions

1. Put MI_INDEX fetching and signature-based normalization in a shared module used by both entry points, avoiding their circular import. Require the returned ISO date to match the requested day and retain the security name and raw fields. A successful no-data response is empty; malformed or unknown nonempty signatures fail.
2. Use TWSE whole-market `code=''`, `period=YYYY-MM-DD` checkpoints alongside existing symbol-month checkpoints. Insert rows with conflict-do-nothing and log both returned and inserted counts. Leave existing rows, timestamps and raw JSON untouched. Bulk and STOCK_DAY retain their existing precedence.
3. Automatic candidates combine either market's observed dates from 2010-01-04 with weekdays in the last 45 calendar days through today and valid unfinished TWSE day checkpoint dates. Unfinished dates remain candidates after the recent window, within the source floor and current-date bound; malformed periods do not become requests. This includes days missing from both markets while avoiding an invented historic holiday calendar. Exclude settled day units, rank no-TWSE-row gaps first then newest first, and process TWSE day units before TPEx units and symbol-months. Recompute automatic TPEx candidates after TWSE day requests so newly recovered days can be repaired in the same invocation.
4. A nonempty TWSE day settles `ok`. An empty day within seven calendar days stays `partial`; an older empty date without existing TWSE rows settles `empty`. An empty response for an existing TWSE day stays retryable regardless of age: existing observations contradict an absence. Failures record `error`. Existing settled legacy checkpoints are preserved.
5. Keep symbol-month candidates to codes with prior nonempty-code TWSE checkpoints plus those on the newest TWSE date, preventing MI_INDEX-only historical names from generating redundant month requests. Missing checkpoint tables remain readable without initializing a read-only query.
6. When successful TPEx bulk supplies a date and TWSE bulk is missing or older, attempt the TPEx date via MI_INDEX and insert only absent rows before the existing rebuild. Missing includes transport/normalization/write failures and responses without usable dated rows. Roll back a failed bulk write before committing another market or the supplement; bulk counts remain separate from supplement insertions. This is opportunistic: there is no inferred wall-clock request date or assumed publication time, empty/error responses log a warning and retain bulk success. Probe publication timing on the next trading day as operational evidence; do not claim it measured on a Sunday.
7. Explicit day selection permits bounded production repair without invoking symbol-month rewriting. Derive the advisory lock from the selected database directory, keeping scratch and production invocations separate. Preserve budgets, retry/backoff and legacy CLI behavior.
8. The production sweep encountered HTTP 200 HTML titled "Website maintenance" after thousands of valid responses. Add optional `--max-consecutive-failures` and a matching keyword argument: a positive threshold stops after the latest failure checkpoint is committed and logs the stopping reason. A successful fetch resets the streak; omitted configuration preserves existing behavior. The limit applies to the requested run and does not reset checkpoints or infer that failed days are empty. Resume uses the original date plan and unsettled checkpoints, without broadening its observation date.

## Risks / Trade-offs

- Historical codes can be reused or change venue: retain names, compare overlapping closes for CSV-backed affected instruments, and report ambiguous identities before derived artifact rebuilds.
- A 2010-to-present sweep requires about 4,100 requests: throttle at two seconds, retain checkpoints, bound it before the next scheduled writer, and prioritize current holes.
- Revenue archives have uncertain early coverage and no demonstrated announcement timestamps: probe actual bytes/schema; retain failures and NULL availability rather than manufacture time.
- Existing institutional/revenue CLIs may refresh populated units: plan only entirely absent units, preserve consistent before snapshots and raw evidence, and rerun audits after downloads.

## Migration Plan

Deliver reviewed code through the task PR; production integration requires owner authorization. Authorized data repair uses explicit primary-checkout artifact paths, never worktree data as the final source. Validate new paths on small scratch databases first, then download absent production units. Preserve existing databases through SQLite-consistent snapshots and report coverage, inserted counts, checkpoint state and affected symbols. Deploy the restricted symbol-month universe before scheduled backfill resumes against expanded history: older code would turn MI_INDEX-only historical codes into redundant STOCK_DAY work. Pause authorized scheduled starts and verify writers have exited before updating production code, then verify existing launcher actions before authorized resumption. No schema migration or checkpoint reset is required; reverting code leaves additional rows readable but restores the redundant-request risk.

## Open Questions

Live MI_INDEX publication time and the earliest observed nonempty revenue CSV months remain to be measured. Neither is inferred from an empty response.
