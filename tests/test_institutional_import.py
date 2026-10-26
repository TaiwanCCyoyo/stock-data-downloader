from __future__ import annotations

import gc
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

import fetch_institutional as institutional
import pytest

from shp_utils import institutional_import as importer
from shp_utils import institutional_schema as schema

RUN_ID = "20261024T212808+0800-test-ab12"


def _twse_payload(codes: dict[str, str]) -> dict[str, Any]:
    rows = [[code, f"n{code}", "10", "4", net, "0", "0", "0", "0", "0", "0", net] for code, net in codes.items()]
    return {"stat": "OK", "fields": list(schema.TWSE_FIELDS_A), "data": rows}


def _add_unit(root: Path, db: Path, market: str, day: str, codes: dict[str, str], *, status: str = "ok") -> dict[str, Any]:
    payload = _twse_payload(codes) if status == "ok" else {"stat": "OK", "fields": [], "data": []}
    raw_rel = f"raw/institutional/{market.lower()}/{day}_20261024T130000000000Z.json"
    raw = root / raw_rel
    raw.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    raw.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    fetched = "2026-10-24T13:00:00.000000+00:00"
    rows, signature = schema.normalize_twse_payload(payload, date.fromisoformat(day), fetched_at=fetched) if status == "ok" else ([], "twse-empty")
    for row in rows:
        row["market"] = market
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={day.replace('-', '')}"
    with closing(institutional.connect(db)) as conn, conn:
        institutional.upsert_rows(conn, rows)
        conn.execute(
            "INSERT INTO institutional_fetch_log (market, date, source_url, status, row_count, schema_signature,"
            " published_at, fetched_at, raw_path, sha256, final_url)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (market, day, url, status, len(rows), signature, None, fetched, raw_rel, sha, url),
        )
    return {
        "market": market,
        "date": day,
        "status": status,
        "rows": len(rows),
        "schema_signature": signature,
        "source_url": url,
        "final_url": url,
        "raw_path": raw_rel,
        "sha256": sha,
    }


def _seal(root: Path, importable: list[dict[str, Any]], unresolved: list[dict[str, Any]]) -> None:
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        files.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": importer.file_sha256(path)})
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    manifest = {"run_id": RUN_ID, "files": files, "payload_digest": {"value": digest}, "importable_units": importable, "unresolved_units": unresolved}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    package = tmp_path / "staging" / RUN_ID
    package.mkdir(parents=True)
    staging_db = package / "institutional.sqlite"
    institutional.init_db(staging_db)
    units = [
        _add_unit(package, staging_db, "TWSE", "2012-05-02", {"1101": "6", "2330": "-3"}),
        _add_unit(package, staging_db, "TWSE", "2012-05-03", {"1101": "1"}),
        _add_unit(package, staging_db, "TPEx", "2014-12-01", {"6488": "2"}),
    ]
    empty = _add_unit(package, staging_db, "TPEx", "2014-07-01", {}, status="partial")
    gc.collect()  # init_db leaves its connection to the garbage collector
    with closing(sqlite3.connect(staging_db)) as conn:
        assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    _seal(package, units, [empty])
    data = tmp_path / "data"
    data.mkdir()
    target = data / "institutional.sqlite"
    institutional.init_db(target)
    with closing(institutional.connect(target)) as conn, conn:
        institutional.upsert_rows(
            conn, schema.normalize_twse_payload(_twse_payload({"2330": "9"}), date(2019, 1, 2), fetched_at="2026-09-03T00:00:00+00:00")[0]
        )
    return {"package": package, "target": target, "data": data, "receipts": tmp_path / "receipts", "units": units}


def _apply(env: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return importer.run_import(importer.load_package(env["package"]), env["target"], cutoff="2018-12-31", receipt_dir=env["receipts"], **kwargs)


def _count(db: Path, sql: str) -> int:
    with closing(sqlite3.connect(db)) as conn:
        return int(conn.execute(sql).fetchone()[0])


def test_dry_run_plans_inserts_and_writes_nothing(env: dict[str, Any]) -> None:
    before = importer.content_digest(env["target"])
    plan = importer.run_import(importer.load_package(env["package"]), env["target"], cutoff="2018-12-31", receipt_dir=None)
    assert [p["kind"] for p in plan["plans"]] == ["insert"] * 3
    assert plan["unresolved_units"] == 1
    assert importer.content_digest(env["target"]) == before
    assert not (env["data"] / "raw").exists()


def test_apply_inserts_relocates_raw_and_preserves_existing(env: dict[str, Any]) -> None:
    before = importer.content_digest(env["target"])
    summary = _apply(env)
    assert summary["by_market_year"]["TWSE"]["2012"]["inserted_rows"] == 3
    assert summary["by_market_year"]["TPEx"]["2014"] == {"inserted_rows": 1, "inserted_units": 1, "unresolved_units": 1}
    after = importer.content_digest(env["target"])
    assert after["institutional_daily"]["TWSE/2019"] == before["institutional_daily"]["TWSE/2019"]
    with closing(sqlite3.connect(env["target"])) as conn:
        logs = conn.execute("SELECT market, date, status, raw_path, sha256 FROM institutional_fetch_log ORDER BY market, date").fetchall()
    assert [row[:3] for row in logs] == [("TPEx", "2014-12-01", "ok"), ("TWSE", "2012-05-02", "ok"), ("TWSE", "2012-05-03", "ok")]
    for _, _, _, raw_path, sha in logs:
        assert raw_path == f"raw/institutional/{raw_path.split('/')[2]}/{Path(raw_path).name}"
        assert raw_path.split("/")[2] in {"twse", "tpex"}
        assert importer.file_sha256(env["data"] / raw_path) == sha
    assert _count(env["target"], "SELECT COUNT(*) FROM institutional_fetch_log WHERE status <> 'ok'") == 0


def test_rerun_is_idempotent(env: dict[str, Any]) -> None:
    _apply(env)
    digest = importer.content_digest(env["target"])
    summary = _apply(env)
    assert summary["by_market_year"]["TWSE"]["2012"] == {"already_imported_rows": 3, "already_imported_units": 2}
    assert importer.content_digest(env["target"]) == digest


def test_equivalent_existing_unit_is_skipped(env: dict[str, Any]) -> None:
    with closing(institutional.connect(env["target"])) as conn, conn:
        rows, sig = schema.normalize_twse_payload(_twse_payload({"1101": "1"}), date(2012, 5, 3), fetched_at="2026-01-01T00:00:00+00:00")
        institutional.upsert_rows(conn, rows)
        conn.execute(
            "INSERT INTO institutional_fetch_log (market, date, source_url, status, row_count, schema_signature, fetched_at, raw_path, sha256, final_url)"
            " VALUES ('TWSE','2012-05-03','u','ok',1,?,?,'raw/institutional/twse/x.json','abc','u')",
            (sig, "2026-01-01T00:00:00+00:00"),
        )
    summary = _apply(env)
    cell = summary["by_market_year"]["TWSE"]["2012"]
    assert cell["equivalent_skip_units"] == 1 and cell["inserted_units"] == 1
    assert _count(env["target"], "SELECT COUNT(*) FROM institutional_fetch_log WHERE raw_path = 'raw/institutional/twse/x.json'") == 1


@pytest.mark.parametrize(
    ("market", "net"),
    [("TWSE", "999"), ("TPEx", "1")],
    ids=["content-differs", "other-market"],
)
def test_conflicting_row_is_reported_and_unit_untouched(env: dict[str, Any], market: str, net: str) -> None:
    with closing(institutional.connect(env["target"])) as conn, conn:
        rows, _ = schema.normalize_twse_payload(_twse_payload({"1101": net}), date(2012, 5, 3), fetched_at="2026-01-01T00:00:00+00:00")
        rows[0]["market"] = market
        institutional.upsert_rows(conn, rows)
    summary = _apply(env)
    assert summary["by_market_year"]["TWSE"]["2012"]["conflict_units"] == 1
    assert _count(env["target"], "SELECT COUNT(*) FROM institutional_fetch_log WHERE date = '2012-05-03'") == 0
    assert _count(env["target"], "SELECT COUNT(*) FROM institutional_daily WHERE date = '2012-05-03'") == 1


def test_raw_name_collision_with_other_bytes_is_a_conflict(env: dict[str, Any]) -> None:
    unit = env["units"][0]
    dest = env["data"] / importer.target_raw_path(unit["raw_path"])
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"different")
    summary = _apply(env)
    assert summary["by_market_year"]["TWSE"]["2012"]["conflict_units"] == 1
    assert dest.read_bytes() == b"different"


def test_failure_rolls_back_batch_and_rerun_completes(env: dict[str, Any]) -> None:
    before = importer.content_digest(env["target"])

    def boom(unit: dict[str, Any]) -> None:
        if unit["date"] == "2012-05-03":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError):
        _apply(env, batch_units=10, fail_after_insert=boom)
    assert importer.content_digest(env["target"]) == before
    events = [json.loads(line)["event"] for line in (env["receipts"] / "import-receipt.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "batch_rolled_back" in events
    summary = _apply(env)
    assert summary["by_market_year"]["TWSE"]["2012"]["inserted_units"] == 2


def test_stopped_gate_leaves_remaining_units_not_started(env: dict[str, Any]) -> None:
    calls = iter([None, "deadline"])
    summary = _apply(env, batch_units=1, may_start_batch=lambda: next(calls))
    assert summary["stopped"] == "deadline"
    assert summary["by_market_year"]["TWSE"]["2012"] == {"inserted_rows": 2, "inserted_units": 1, "not_started_rows": 1, "not_started_units": 1}


def test_changed_package_and_late_units_are_refused(env: dict[str, Any]) -> None:
    manifest = json.loads((env["package"] / "manifest.json").read_text(encoding="utf-8"))
    late = dict(env["units"][1], date="2019-01-02")
    manifest["importable_units"].append(late)
    (env["package"] / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    plan = importer.run_import(importer.load_package(env["package"]), env["target"], cutoff="2018-12-31", receipt_dir=None)
    assert any("after cutoff" in item["reason"] for item in plan["rejected"])
    (env["package"] / env["units"][0]["raw_path"]).write_bytes(b"tampered")
    with pytest.raises(importer.InstitutionalImportError):
        importer.load_package(env["package"])


def test_backup_snapshot_matches_live(env: dict[str, Any], tmp_path: Path) -> None:
    record = importer.backup_database(env["target"], tmp_path / "backup" / "snap")
    assert record["integrity_check"] == ["ok"] and record["content_digest_matches_live"]
    with pytest.raises(importer.InstitutionalImportError):
        importer.backup_database(env["target"], env["data"] / "inside")
