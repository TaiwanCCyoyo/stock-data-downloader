from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import fetch_monthly_revenue as revenue
import pytest


def sample_row(*, code: str = "2330", month: str = "11507", current: str = "100,000") -> dict[str, str]:
    return {
        "出表日期": "1150810",
        "資料年月": month,
        "公司代號": code,
        "公司名稱": "測試公司",
        "產業別": "半導體業",
        "營業收入-當月營收": current,
        "營業收入-上月營收": "90,000",
        "營業收入-去年當月營收": "80,000",
        "營業收入-上月比較增減(%)": "11.11",
        "營業收入-去年同月增減(%)": "25.0",
        "累計營業收入-當月累計營收": "700,000",
        "累計營業收入-去年累計營收": "600,000",
        "累計營業收入-前期比較增減(%)": "16.67",
        "備註": "-",
    }


def csv_bytes(*rows: dict[str, str], encoding: str = "utf-8-sig") -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode(encoding)


def ingest(
    tmp_path: Path,
    row: dict[str, str],
    *,
    source: str = revenue.SOURCE_LATEST,
    availability_kind: str = revenue.AVAILABILITY_FIRST_OBSERVED,
    observed_at: str = "2026-08-10T09:00:00+00:00",
) -> tuple[Path, Path]:
    db_path = tmp_path / "monthly.sqlite"
    raw_dir = tmp_path / "raw"
    payload = [row]
    revenue.ingest_payload(
        payload=payload,
        raw_bytes=json.dumps(payload, ensure_ascii=False).encode(),
        raw_extension="json",
        market=revenue.MARKET_TWSE,
        source=source,
        url="https://example.test/revenue",
        availability_kind=availability_kind,
        observed_at=observed_at,
        db_path=db_path,
        raw_dir=raw_dir,
        expected_revenue_month="2026-07" if source == revenue.SOURCE_HISTORICAL else None,
    )
    return db_path, raw_dir


def test_parse_source_month_supports_roc_and_iso_values():
    assert revenue.parse_source_month("11507") == "2026-07"
    assert revenue.parse_source_month("2026-07") == "2026-07"


def test_parse_source_month_reads_unpadded_historical_csv_month():
    """The historical MOPS CSV writes `108/1`, not `108/01`.

    Observed live on 2026-09-12 in t21sc03_108_1.csv for both sii and otc. The
    previous rule stripped the separator and then branched on the remaining
    length, so this shape collapsed to four digits and raised, while a two-digit
    month such as `108/10` collapsed to five and parsed.

    That is a statement about the parser, which is all this test can show. It
    says nothing about whether any historical backfill was ever run, or about
    what the database does or does not contain.
    """
    assert revenue.parse_source_month("108/1") == "2019-01"
    assert revenue.parse_source_month("112/12") == "2023-12"
    assert revenue.parse_source_month("115/9") == "2026-09"


def test_parse_source_month_rejects_invalid_month():
    with pytest.raises(ValueError, match="invalid revenue month"):
        revenue.parse_source_month("11513")
    with pytest.raises(ValueError, match="invalid revenue month"):
        revenue.parse_source_month("108/0")
    with pytest.raises(ValueError, match="invalid revenue month"):
        revenue.parse_source_month("108/13")
    # A separator does not license an arbitrary left-hand side.
    with pytest.raises(ValueError, match="invalid revenue month"):
        revenue.parse_source_month("20260701/1")
    with pytest.raises(ValueError, match="invalid revenue month"):
        revenue.parse_source_month("2026-07-01")


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_parse_percentage_rejects_non_finite_values(value: str):
    with pytest.raises(ValueError, match="invalid percentage value"):
        revenue.parse_percentage(value)


def test_parse_csv_payload_accepts_cp950():
    payload = revenue.parse_csv_payload(csv_bytes(sample_row(), encoding="cp950"))
    assert payload[0]["公司代號"] == "2330"


def test_parse_json_payload_accepts_utf8_bom():
    raw = json.dumps([sample_row()], ensure_ascii=False).encode("utf-8-sig")
    assert revenue.parse_json_payload(raw)[0]["公司代號"] == "2330"


def test_normalization_rejects_excessive_row_and_field_counts(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(revenue, "MAX_PAYLOAD_ROWS", 1)
    with pytest.raises(ValueError, match="row limit"):
        revenue.normalize_payload(
            [sample_row(), sample_row(code="2317")],
            market=revenue.MARKET_TWSE,
            source=revenue.SOURCE_LATEST,
            availability_kind=revenue.AVAILABILITY_FIRST_OBSERVED,
            observed_at="2026-08-10T09:00:00+00:00",
        )

    monkeypatch.setattr(revenue, "MAX_PAYLOAD_ROWS", 10)
    monkeypatch.setattr(revenue, "MAX_FIELDS_PER_ROW", 1)
    with pytest.raises(ValueError, match="field limit"):
        revenue.normalize_payload(
            [sample_row()],
            market=revenue.MARKET_TWSE,
            source=revenue.SOURCE_LATEST,
            availability_kind=revenue.AVAILABILITY_FIRST_OBSERVED,
            observed_at="2026-08-10T09:00:00+00:00",
        )


def test_normalization_preserves_units_and_source_period():
    rows = revenue.normalize_payload(
        [sample_row()],
        market=revenue.MARKET_TWSE,
        source=revenue.SOURCE_LATEST,
        availability_kind=revenue.AVAILABILITY_FIRST_OBSERVED,
        observed_at="2026-08-10T09:00:00+00:00",
    )

    assert rows[0]["revenue_month"] == "2026-07"
    assert rows[0]["current_revenue_k_twd"] == 100_000
    assert rows[0]["source_report_date"] == "2026-08-10"
    assert rows[0]["note"] is None


def test_latest_observation_has_conservative_availability_time(tmp_path: Path):
    db_path, _raw_dir = ingest(tmp_path, sample_row())

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT announcement_at, available_at, availability_kind FROM monthly_revenue_current").fetchone()

    assert row == (None, "2026-08-10T09:00:00+00:00", revenue.AVAILABILITY_FIRST_OBSERVED)


def test_historical_row_does_not_fabricate_availability(tmp_path: Path):
    db_path, _raw_dir = ingest(
        tmp_path,
        sample_row(),
        source=revenue.SOURCE_HISTORICAL,
        availability_kind=revenue.AVAILABILITY_HISTORICAL_UNKNOWN,
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT announcement_at, available_at, availability_kind FROM monthly_revenue_current").fetchone()

    assert row == (None, None, revenue.AVAILABILITY_HISTORICAL_UNKNOWN)


def test_identical_refetch_is_idempotent(tmp_path: Path):
    db_path, raw_dir = ingest(tmp_path, sample_row())
    ingest(
        tmp_path,
        sample_row(),
        observed_at="2026-08-10T10:00:00+00:00",
    )

    with sqlite3.connect(db_path) as conn:
        current_count = conn.execute("SELECT COUNT(*) FROM monthly_revenue_current").fetchone()[0]
        revision_count = conn.execute("SELECT COUNT(*) FROM monthly_revenue_revisions").fetchone()[0]
        last_fetched = conn.execute("SELECT last_fetched_at FROM monthly_revenue_current").fetchone()[0]
        snapshot_count = conn.execute("SELECT COUNT(*) FROM monthly_revenue_raw_snapshots").fetchone()[0]

    assert current_count == 1
    assert revision_count == 1
    assert last_fetched == "2026-08-10T10:00:00+00:00"
    assert snapshot_count == 2
    assert len(list(raw_dir.rglob("*.json"))) == 2


def test_correction_appends_revision_before_updating_current(tmp_path: Path):
    db_path, _raw_dir = ingest(tmp_path, sample_row(current="100,000"))
    ingest(tmp_path, sample_row(current="101,000"), observed_at="2026-08-11T09:00:00+00:00")

    with sqlite3.connect(db_path) as conn:
        current = conn.execute("SELECT current_revenue_k_twd, revision_no FROM monthly_revenue_current").fetchone()
        revisions = conn.execute("SELECT current_revenue_k_twd FROM monthly_revenue_revisions ORDER BY revision_no").fetchall()

    assert current == (101_000, 2)
    assert revisions == [(100_000,), (101_000,)]


def test_batch_report_date_change_does_not_create_a_business_revision(tmp_path: Path):
    db_path, _raw_dir = ingest(tmp_path, sample_row())
    refreshed = sample_row()
    refreshed["出表日期"] = "1150811"
    ingest(tmp_path, refreshed, observed_at="2026-08-11T09:00:00+00:00")

    with sqlite3.connect(db_path) as conn:
        current = conn.execute("SELECT source_report_date, revision_no FROM monthly_revenue_current").fetchone()
        revision_count = conn.execute("SELECT COUNT(*) FROM monthly_revenue_revisions").fetchone()[0]

    assert current == ("2026-08-11", 1)
    assert revision_count == 1


def test_latest_fetch_upgrades_current_without_mutating_historical_revision(tmp_path: Path):
    db_path, _raw_dir = ingest(
        tmp_path,
        sample_row(),
        source=revenue.SOURCE_HISTORICAL,
        availability_kind=revenue.AVAILABILITY_HISTORICAL_UNKNOWN,
    )
    ingest(tmp_path, sample_row(), observed_at="2026-08-11T09:00:00+00:00")

    with sqlite3.connect(db_path) as conn:
        current = conn.execute("SELECT revision_no, available_at, availability_kind FROM monthly_revenue_current").fetchone()
        revision = conn.execute("SELECT available_at, availability_kind FROM monthly_revenue_revisions").fetchone()

    assert current == (1, "2026-08-11T09:00:00+00:00", revenue.AVAILABILITY_FIRST_OBSERVED)
    assert revision == (None, revenue.AVAILABILITY_HISTORICAL_UNKNOWN)


def test_invalid_payload_records_failed_raw_snapshot_without_changing_rows(tmp_path: Path):
    db_path, raw_dir = ingest(tmp_path, sample_row())
    invalid = [{"公司代號": "2330"}]

    with pytest.raises(ValueError, match="missing required fields"):
        revenue.ingest_payload(
            payload=invalid,
            raw_bytes=json.dumps(invalid, ensure_ascii=False).encode(),
            raw_extension="json",
            market=revenue.MARKET_TWSE,
            source=revenue.SOURCE_LATEST,
            url="https://example.test/revenue",
            availability_kind=revenue.AVAILABILITY_FIRST_OBSERVED,
            observed_at="2026-08-12T09:00:00+00:00",
            db_path=db_path,
            raw_dir=raw_dir,
        )

    with sqlite3.connect(db_path) as conn:
        current_count = conn.execute("SELECT COUNT(*) FROM monthly_revenue_current").fetchone()[0]
        statuses = conn.execute("SELECT status FROM monthly_revenue_raw_snapshots ORDER BY fetched_at").fetchall()

    assert current_count == 1
    assert statuses == [("SUCCESS",), ("FAILED",)]


def test_raw_snapshot_preserves_exact_response_bytes_and_hash(tmp_path: Path):
    payload = [sample_row()]
    raw_bytes = ("\ufeff  " + json.dumps(payload, ensure_ascii=False, indent=2)).encode("utf-8")
    db_path = tmp_path / "monthly.sqlite"
    raw_dir = tmp_path / "raw"

    revenue.ingest_payload(
        payload=payload,
        raw_bytes=raw_bytes,
        raw_extension="json",
        market=revenue.MARKET_TWSE,
        source=revenue.SOURCE_LATEST,
        url="https://example.test/revenue",
        availability_kind=revenue.AVAILABILITY_FIRST_OBSERVED,
        observed_at="2026-08-12T09:00:00+00:00",
        db_path=db_path,
        raw_dir=raw_dir,
    )

    with sqlite3.connect(db_path) as conn:
        raw_path, stored_hash = conn.execute("SELECT raw_path, sha256 FROM monthly_revenue_raw_snapshots").fetchone()

    assert Path(raw_path).read_bytes() == raw_bytes
    assert stored_hash == hashlib.sha256(raw_bytes).hexdigest()


def test_raw_snapshot_rejects_link_or_reparse_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    raw_dir = tmp_path / "raw"

    def fake_is_link(path: Path) -> bool:
        return path.name == revenue.SOURCE_LATEST

    monkeypatch.setattr(revenue, "_is_link_or_reparse", fake_is_link)

    with pytest.raises(RuntimeError, match="must not contain links"):
        revenue.write_raw_snapshot(
            raw_bytes=b"payload",
            raw_dir=raw_dir,
            source=revenue.SOURCE_LATEST,
            market=revenue.MARKET_TWSE,
            revenue_month=None,
            fetched_at="2026-08-12T09:00:00+00:00",
            sha256=hashlib.sha256(b"payload").hexdigest(),
            extension="json",
        )


def test_historical_period_mismatch_rolls_back_normalized_rows(tmp_path: Path):
    payload = [sample_row(month="11506")]
    db_path = tmp_path / "monthly.sqlite"

    with pytest.raises(ValueError, match="period mismatch"):
        revenue.ingest_payload(
            payload=payload,
            raw_bytes=csv_bytes(*payload),
            raw_extension="csv",
            market=revenue.MARKET_TWSE,
            source=revenue.SOURCE_HISTORICAL,
            url="https://example.test/history.csv",
            availability_kind=revenue.AVAILABILITY_HISTORICAL_UNKNOWN,
            observed_at="2026-08-12T09:00:00+00:00",
            db_path=db_path,
            raw_dir=tmp_path / "raw",
            expected_revenue_month="2026-07",
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM monthly_revenue_current").fetchone()[0] == 0


def test_refresh_latest_fetches_both_markets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    requested: list[str] = []

    def fake_fetch(url: str, **_kwargs):
        requested.append(url)
        code = "2330" if url.endswith("_L") else "6488"
        payload = json.dumps([sample_row(code=code)], ensure_ascii=False).encode()
        return payload, url

    monkeypatch.setattr(revenue, "fetch_bytes_with_retry_url", fake_fetch)

    counts = revenue.refresh_latest(
        db_path=tmp_path / "monthly.sqlite",
        raw_dir=tmp_path / "raw",
        now=datetime(2026, 8, 10, 9, tzinfo=UTC),
    )

    assert counts == {revenue.MARKET_TPEX: 1, revenue.MARKET_TWSE: 1}
    assert {url.rsplit("_", 1)[-1] for url in requested} == {"L", "O"}


def test_refresh_latest_timestamps_only_after_each_response(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    events: list[str] = []

    def fake_fetch(url: str, **_kwargs):
        events.append(f"fetch:{url[-1]}")
        code = "2330" if url.endswith("_L") else "6488"
        return json.dumps([sample_row(code=code)], ensure_ascii=False).encode(), url

    def fake_now(_fixed: datetime | None = None) -> str:
        events.append("clock")
        return "2026-08-10T09:00:00+00:00"

    monkeypatch.setattr(revenue, "fetch_bytes_with_retry_url", fake_fetch)
    monkeypatch.setattr(revenue, "_utc_now", fake_now)

    revenue.refresh_latest(db_path=tmp_path / "monthly.sqlite", raw_dir=tmp_path / "raw")

    assert events == ["fetch:O", "clock", "fetch:L", "clock"]


def test_refresh_latest_rolls_back_both_markets_when_second_payload_is_invalid(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db_path = tmp_path / "monthly.sqlite"
    raw_dir = tmp_path / "raw"
    revenue.ingest_payload(
        payload=[sample_row(code="6488", current="90,000")],
        raw_bytes=b"seed",
        raw_extension="json",
        market=revenue.MARKET_TPEX,
        source=revenue.SOURCE_LATEST,
        url="https://example.test/seed",
        availability_kind=revenue.AVAILABILITY_FIRST_OBSERVED,
        observed_at="2026-08-09T09:00:00+00:00",
        db_path=db_path,
        raw_dir=raw_dir,
    )

    def fake_fetch(url: str, **_kwargs):
        if url.endswith("_O"):
            payload = [sample_row(code="6488", current="100,000")]
        else:
            payload = [sample_row(code="2330"), sample_row(code="2330", current="101,000")]
        return json.dumps(payload, ensure_ascii=False).encode(), url

    monkeypatch.setattr(revenue, "fetch_bytes_with_retry_url", fake_fetch)

    with pytest.raises(ValueError, match="duplicate monthly revenue key"):
        revenue.refresh_latest(db_path=db_path, raw_dir=raw_dir)

    with sqlite3.connect(db_path) as conn:
        current = conn.execute("SELECT current_revenue_k_twd, revision_no FROM monthly_revenue_current WHERE market = 'TPEx'").fetchone()
        failed = conn.execute("SELECT COUNT(*) FROM monthly_revenue_raw_snapshots WHERE status = 'FAILED'").fetchone()[0]

    assert current == (90_000, 1)
    assert failed == 2


def test_refresh_latest_preserves_malformed_response_as_failed_raw(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    raw_bytes = b"<html>not JSON</html>"

    def fake_fetch(url: str, **_kwargs):
        return raw_bytes, url

    monkeypatch.setattr(revenue, "fetch_bytes_with_retry_url", fake_fetch)
    db_path = tmp_path / "monthly.sqlite"
    raw_dir = tmp_path / "raw"

    with pytest.raises(ValueError, match="not valid UTF-8 JSON"):
        revenue.refresh_latest(db_path=db_path, raw_dir=raw_dir)

    with sqlite3.connect(db_path) as conn:
        snapshot = conn.execute("SELECT status, raw_path, sha256 FROM monthly_revenue_raw_snapshots").fetchone()

    assert snapshot[0] == "FAILED"
    assert Path(snapshot[1]).read_bytes() == raw_bytes
    assert snapshot[2] == hashlib.sha256(raw_bytes).hexdigest()


def test_duplicate_business_key_rejects_entire_snapshot(tmp_path: Path):
    payload = [sample_row(), sample_row(current="101,000")]

    with pytest.raises(ValueError, match="duplicate monthly revenue key"):
        revenue.ingest_payload(
            payload=payload,
            raw_bytes=json.dumps(payload, ensure_ascii=False).encode(),
            raw_extension="json",
            market=revenue.MARKET_TWSE,
            source=revenue.SOURCE_LATEST,
            url="https://example.test/revenue",
            availability_kind=revenue.AVAILABILITY_FIRST_OBSERVED,
            observed_at="2026-08-12T09:00:00+00:00",
            db_path=tmp_path / "monthly.sqlite",
            raw_dir=tmp_path / "raw",
        )

    with sqlite3.connect(tmp_path / "monthly.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM monthly_revenue_current").fetchone()[0] == 0


def test_backfill_is_bounded_and_resumes_from_checkpoints(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    requested: list[str] = []

    def fake_fetch(url: str, **_kwargs):
        requested.append(url)
        month = "11506" if "_6.csv" in url else "11507"
        return csv_bytes(sample_row(month=month)), url

    monkeypatch.setattr(revenue, "fetch_bytes_with_retry_url", fake_fetch)
    db_path = tmp_path / "monthly.sqlite"
    raw_dir = tmp_path / "raw"

    first = revenue.backfill(
        start_month="2026-06",
        end_month="2026-07",
        markets=[revenue.MARKET_TWSE],
        max_requests=1,
        sleep=0,
        db_path=db_path,
        raw_dir=raw_dir,
    )
    second = revenue.backfill(
        start_month="2026-06",
        end_month="2026-07",
        markets=[revenue.MARKET_TWSE],
        max_requests=1,
        sleep=0,
        db_path=db_path,
        raw_dir=raw_dir,
    )

    assert first == {"fetched": 1, "skipped": 0, "rows": 1, "remaining": 1}
    assert second == {"fetched": 1, "skipped": 1, "rows": 1, "remaining": 0}
    assert len(requested) == 2


def test_status_reports_coverage(tmp_path: Path):
    db_path, _raw_dir = ingest(tmp_path, sample_row())

    result = revenue.status(db_path)

    assert result == {
        "rows": 1,
        "symbols": 1,
        "earliest_month": "2026-07",
        "latest_month": "2026-07",
        "revisions": 1,
        "raw_snapshots": 1,
        "availability": {revenue.AVAILABILITY_FIRST_OBSERVED: 1},
    }


def test_status_for_missing_database_is_empty(tmp_path: Path):
    assert revenue.status(tmp_path / "missing.sqlite")["rows"] == 0
