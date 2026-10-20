from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import import_corporate_action_supplement as cli
import pytest
from fetch_corporate_actions import init_db

from shp_utils.corporate_action_supplement import SupplementError, extract, in_producer_write_window, load_batch, write

BATCH = "test-batch-v1"
TWT49U_FIELDS = [
    "股票代號",
    "股票名稱",
    "(每股配發現金股利)除息",
    "(增資配股) 除權",
    "A. 按普通股股東持股比例每千股無償配股",
    "B. 員工紅利轉增資",
    "C. (有償) 現金增資",
    "每股認購金額",
    "a. 公開承銷",
    "b. 員工認購",
    " c. 原股東認購",
    "按股東持股比例每千股認購",
]
EXDAILYQ_FIELDS = [
    "除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價", "權值", "息值", "權值+息值", "權/息", "漲停價", "跌停價",
    "開始交易基準價", "減除股利參考價", "現金股利", "每仟股無償配股", "現金增資股數", "現金增資認購價", "公開承銷股數",
    "員工認購股數", "原股東認購股數", "按持股比例仟股認購",
]  # fmt: skip
SUBJECT = "公告本公司配發現金股利暨盈餘轉增資發行新股基準日"
DETAIL_PARAMETERS = {"detailReportKind": "A", "declarationDate": "1050810", "etn_no": "", "companyId": "3205", "serialNumber": 1}


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _mops_list() -> bytes:
    titles: list[dict[str, Any]] = [{"main": name, "sub": []} for name in ("公告日期", "申報序號", "主旨", "種類", "內容", "修正後現金股利發放日")]
    row = ["105/08/10", "1", SUBJECT, "", {"apiName": "t108sb19_detail", "parameters": DETAIL_PARAMETERS}, "無"]
    return _dump({"code": 200, "result": {"companyId": "3205", "recordDateAnnouncement": {"titles": titles, "data": [row]}}})


def _mops_detail(subscription_price: str = "133.00") -> bytes:
    rights = "\r\n".join([
        "※除權--普通股：每壹股配發股票(股利)0.48659339元（即每壹仟股無償配發盈餘轉增資48.65933900股，"
        f"每壹仟股無償配發法定盈餘公積、資本公積轉增資0.00000000股），每壹仟股以每股新台幣{subscription_price}元認購 普通股 54.55595036股",
        "※除息--普通股：每壹股配發現金(股利)2.91956063元，（即每股盈餘分配2.91956063元，每股法定盈餘公積、資本公積發放0.00000000元)，現金配發總額85,623,969元",
        "＊現金股利發放日：105年9月10日（發放現金股利時適用。）",
    ])  # fmt: skip
    titles = ["一、公告序號：", "二、主旨：", "（五）權利分派內容：", "（八）權利分派基準日："]
    data = [[1, SUBJECT, rights, "105年8月23日\r\n除權/除息交易日：105年8月17日"]]
    return _dump({"code": 200, "result": {"companyId": "3205", "titles": [{"main": t, "sub": []} for t in titles], "data": data}})


def _exdailyq(rows: list[list[str]] | None = None) -> bytes:
    if rows is None:
        rows = [[
            "105/08/17", "3205", "佰研", "169.50", "157.27", "9.31", "2.91956063", "12.23", "除權息", "174.50", "142.00", "159.00",
            "158.85", "2.91956063", "48.65933867", "2000000", "133.00", "200000", "200000", "1600000", "54.55595037",
        ]]  # fmt: skip
    return _dump({"stat": "ok", "tables": [{"fields": EXDAILYQ_FIELDS, "data": rows}]})


def _twse_detail(*, reversed_fields: bool = False, stat: str = "ok") -> bytes:
    row = ["1519  ", "華城", "6 元／股", "", "100 股", "0 股", "0 股", "0 元／股", "0 股", "0 股", "0 股", "0.00000000 股"]
    fields = TWT49U_FIELDS
    if reversed_fields:
        fields, row = fields[::-1], row[::-1]
    return _dump({"stat": stat, "fields": fields, "data": [row]})


def _source(identifier: str, path: str, body: bytes, publisher: str = "TPEx") -> dict[str, Any]:
    return {
        "evidence_id": identifier,
        "publisher": publisher,
        "evidence_kind": "official_announcement",
        "source_url": f"https://example.invalid/{identifier}",
        "retrieved_at_utc": "2026-10-13T00:00:00Z",
        "raw_body": {"path": path, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()},
    }


def _candidate(members: dict[str, bytes], *, cash: str = "2.91956063", extra_fact: dict[str, Any] | None = None) -> bytes:
    notice = members["captured/notice-6276.html"]
    sources = [
        _source("ev-notice", "notice-6276.html", notice),
        _source("ev-mops", "mops/response-002.json", members["captured/mops/response-002.json"], "MOPS"),
        _source("ev-ex", "tpex/exdailyq-3205.json", members["captured/tpex/exdailyq-3205.json"]),
    ]
    facts = [{"attribute": "shares_reduced_per_1000_existing", "raw_value": "528.4689042", "fact_status": "source_explicit", "evidence_ids": ["ev-notice"]}]
    if extra_fact is not None:
        facts.append(extra_fact)
    events = [
        {
            "event_id": "evt-6276",
            "code": "6276",
            "market": "TPEx",
            "event_type": "LOSS_OFFSET_CAPITAL_REDUCTION",
            "date_roles": [{"role": "resumption_date", "date": "2012-07-27"}],
            "source_facts": facts,
            "market_intervals": [
                {
                    "type": "trading_suspension",
                    "start_date": "2012-07-16",
                    "start_inclusive": True,
                    "end_date": "2012-07-26",
                    "end_inclusive": True,
                    "resumption_date": "2012-07-27",
                    "evidence_ids": ["ev-notice"],
                },
                {
                    "type": "trading_suspension",
                    "start_date": "2013-01-02",
                    "start_inclusive": True,
                    "end_date": None,
                    "end_inclusive": None,
                    "resumption_date": None,
                    "evidence_ids": ["ev-notice"],
                },
            ],
        },
        {
            "event_id": "evt-3205",
            "code": "3205",
            "market": "TPEx",
            "event_type": "EX_RIGHT_AND_DIVIDEND",
            "date_roles": [{"role": "study_input_ex_date", "date": "2016-08-17"}],
            "source_facts": [
                {"attribute": "cash_dividend_per_share", "raw_value": cash, "fact_status": "source_explicit", "evidence_ids": ["ev-mops", "ev-ex"]}
            ],
            "market_intervals": [],
        },
        {
            "event_id": "evt-close",
            "code": None,
            "market": "TWSE",
            "event_type": "MARKET_CLOSURE",
            "date_roles": [],
            "source_facts": [{"attribute": "market_closed", "raw_value": "2024-07-24", "fact_status": "source_explicit", "evidence_ids": ["ev-notice"]}],
            "market_intervals": [
                {
                    "type": "market_wide_closure",
                    "start_date": "2024-07-24",
                    "start_inclusive": True,
                    "end_date": "2024-07-25",
                    "end_inclusive": True,
                    "resumption_date": "2024-07-26",
                    "evidence_ids": ["ev-notice"],
                }
            ],
        },
    ]
    return _dump({"schema_version": "annual-winner-supplement-candidate.v1", "sources": sources, "events": events})


def _write_batch(data_dir: Path, *, twse_reversed_fields: bool = False, twse_stat: str = "ok", **candidate_options: Any) -> Path:
    root = data_dir / "corporate_action_supplements" / BATCH
    detail = _mops_detail()
    exdailyq = _exdailyq()
    twse = _twse_detail(reversed_fields=twse_reversed_fields, stat=twse_stat)
    members: dict[str, bytes] = {
        "captured/notice-6276.html": "<p>減資每仟股減少528.4689042股</p>".encode(),
        "captured/mops/response-001.json": _mops_list(),
        "captured/mops/response-002.json": detail,
        "captured/tpex/exdailyq-3205.json": exdailyq,
        "captured/capture-1519/raw-001.json": twse,
    }
    members["captured/mops/manifest.json"] = _dump({
        "schema_version": "mops-dividend-evidence.v1",
        "company_id": "3205",
        "requests": [
            {"raw_file": "response-001.json", "outcome": "validated_list", "request_parameters": {"companyId": "3205"}},
            {"raw_file": "response-002.json", "outcome": "validated_detail", "request_parameters": DETAIL_PARAMETERS},
        ],
    })
    members["captured/tpex/exdailyq-3205.manifest.json"] = _dump({
        "request_url": "https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?response=json",
        "raw_file": "exdailyq-3205.json",
        "sha256": hashlib.sha256(exdailyq).hexdigest(),
    })
    response = hashlib.sha256(twse).hexdigest()
    members["captured/capture-1519/manifest.json"] = _dump({"schema_version": "corporate-detail-capture-v1"})
    members["captured/capture-1519/requests.jsonl"] = _dump({
        "source": "twt49u",
        "code": "1519",
        "raw_file": "raw-001.json",
        "response_sha256": response,
        "outcome": "captured",
    })
    members["captured/capture-1519/events.jsonl"] = _dump({"code": "1519", "effective_date_from_annual_row": "2024-07-25", "response_sha256": response})
    members["captured/candidate.json"] = _candidate(members, **candidate_options)
    for name, body in members.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    publication = {
        "schema": "annual-source-evidence.v1",
        "batch_id": BATCH,
        "candidate": "captured/candidate.json",
        "members": {name: {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()} for name, body in members.items()},
    }
    (root / "publication.json").write_bytes(_dump(publication))
    return root


def _database(data_dir: Path) -> Path:
    db = data_dir / "corporate_actions.sqlite"
    init_db(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute(
            "INSERT INTO corporate_actions (source, market, code, ex_date, event_type, raw_fields_json, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("tpex_exdailyq", "TPEx", "3205", "2016-08-17", "EX_RIGHT_AND_DIVIDEND", "{}", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
    return db


def _import(data_dir: Path, db: Path) -> str:
    batch = load_batch(data_dir, BATCH)
    with closing(sqlite3.connect(db, isolation_level=None)) as conn:
        return write(conn, batch, extract(batch), imported_at="2026-10-20T00:00:00+00:00")


def _terms(db: Path, code: str) -> dict[tuple[str, str], tuple[Any, ...]]:
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "SELECT source_kind, term, value, unit, value_status, extraction, event_date FROM corporate_action_terms WHERE code = ?", (code,)
        ).fetchall()
    return {(row[0], row[1]): row[2:] for row in rows}


def test_imports_each_source_separately_with_exact_source_decimals(tmp_path: Path) -> None:
    _write_batch(tmp_path)
    db = _database(tmp_path)

    assert _import(tmp_path, db) == "inserted"

    terms = _terms(db, "3205")
    # The issuer and the exchange disagree in the eighth decimal; both are kept, neither averaged.
    assert terms[("mops_t108sb19_detail", "subscription_shares_per_1000")][:3] == ("54.55595036", "shares_per_1000_held", "stated")
    assert terms[("tpex_exdailyq", "subscription_shares_per_1000")][:3] == ("54.55595037", "shares_per_1000_held", "stated")
    assert terms[("mops_t108sb19_detail", "earnings_free_shares_per_1000")][0] == "48.65933900"
    assert terms[("tpex_exdailyq", "free_shares_per_1000")][0] == "48.65933867"
    assert terms[("mops_t108sb19_detail", "record_date")][0] == "2016-08-23"
    assert terms[("tpex_exdailyq", "cash_dividend_per_share")][-1] == "2016-08-17"
    twse = _terms(db, "1519")
    assert twse[("twse_twt49u_detail", "cash_dividend_per_share")][:3] == ("6", "NTD_per_share", "stated")
    assert twse[("twse_twt49u_detail", "subscription_shares_per_1000")][0] == "0.00000000"
    curated = _terms(db, "6276")
    assert curated[("official_notice", "shares_reduced_per_1000")] == ("528.4689042", "shares_per_1000_held", "stated", "curated_transcription", "2012-07-27")
    # The 3205 transcription cites parsed documents, so it is checked rather than stored twice.
    assert not any(kind == "official_notice" for kind, _ in terms)


def test_interruptions_keep_open_ends_and_whole_market_closures(tmp_path: Path) -> None:
    _write_batch(tmp_path)
    db = _database(tmp_path)
    _import(tmp_path, db)

    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "SELECT market, code, interruption_type, start_date, end_date, resumption_date FROM trading_interruptions ORDER BY start_date"
        ).fetchall()
    assert rows == [
        ("TPEx", "6276", "trading_suspension", "2012-07-16", "2012-07-26", "2012-07-27"),
        ("TPEx", "6276", "trading_suspension", "2013-01-02", None, None),
        ("TWSE", "", "market_closure", "2024-07-24", "2024-07-25", "2024-07-26"),
    ]


def test_blank_source_terms_stay_null_not_zero(tmp_path: Path) -> None:
    root = _write_batch(tmp_path)
    detail = _mops_detail(subscription_price="")
    (root / "captured/mops/response-002.json").write_bytes(detail)
    _rewrite_identity(root, "captured/mops/response-002.json", detail, candidate_paths={"mops/response-002.json"})
    db = _database(tmp_path)
    _import(tmp_path, db)

    assert _terms(db, "3205")[("mops_t108sb19_detail", "subscription_price")][:3] == (None, "NTD_per_share", "blank")


def _rewrite_identity(root: Path, member: str, body: bytes, *, candidate_paths: frozenset[str] | set[str] = frozenset()) -> None:
    candidate_file = root / "captured/candidate.json"
    candidate = json.loads(candidate_file.read_bytes())
    for source in candidate["sources"]:
        if source["raw_body"]["path"] in candidate_paths:
            source["raw_body"].update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    candidate_body = _dump(candidate)
    candidate_file.write_bytes(candidate_body)
    publication = json.loads((root / "publication.json").read_bytes())
    for name, value in ((member, body), ("captured/candidate.json", candidate_body)):
        publication["members"][name] = {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    (root / "publication.json").write_bytes(_dump(publication))


def test_existing_corporate_actions_rows_are_untouched(tmp_path: Path) -> None:
    _write_batch(tmp_path)
    db = _database(tmp_path)
    with closing(sqlite3.connect(db)) as conn:
        before = conn.execute("SELECT * FROM corporate_actions").fetchall()

    _import(tmp_path, db)

    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT * FROM corporate_actions").fetchall() == before


def test_reimport_is_a_noop_and_changed_content_is_refused(tmp_path: Path) -> None:
    root = _write_batch(tmp_path)
    db = _database(tmp_path)
    assert _import(tmp_path, db) == "inserted"
    assert _import(tmp_path, db) == "unchanged"

    exdailyq = _exdailyq([["105/08/17", "3205", "佰研", *["1"] * 5, "除權息", *["1"] * 12]])
    (root / "captured/tpex/exdailyq-3205.json").write_bytes(exdailyq)
    manifest = json.loads((root / "captured/tpex/exdailyq-3205.manifest.json").read_bytes())
    manifest["sha256"] = hashlib.sha256(exdailyq).hexdigest()
    manifest_body = _dump(manifest)
    (root / "captured/tpex/exdailyq-3205.manifest.json").write_bytes(manifest_body)
    _rewrite_identity(root, "captured/tpex/exdailyq-3205.json", exdailyq, candidate_paths={"tpex/exdailyq-3205.json"})
    _rewrite_identity(root, "captured/tpex/exdailyq-3205.manifest.json", manifest_body)
    before = _terms(db, "3205")

    with pytest.raises(SupplementError):
        _import(tmp_path, db)
    assert _terms(db, "3205") == before


def test_member_bytes_must_match_the_publication(tmp_path: Path) -> None:
    root = _write_batch(tmp_path)
    (root / "captured/notice-6276.html").write_bytes(b"<p>changed</p>")

    with pytest.raises(SupplementError, match="member identity mismatch"):
        load_batch(tmp_path, BATCH)


def test_member_paths_cannot_escape_the_batch(tmp_path: Path) -> None:
    root = _write_batch(tmp_path)
    publication = json.loads((root / "publication.json").read_bytes())
    publication["members"]["../outside.json"] = {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}
    (root / "publication.json").write_bytes(_dump(publication))

    with pytest.raises(SupplementError, match="unsafe member path"):
        load_batch(tmp_path, BATCH)


def test_a_transcription_that_disagrees_with_the_parsed_source_stops_the_import(tmp_path: Path) -> None:
    _write_batch(tmp_path, cash="2.92")

    with pytest.raises(SupplementError, match="transcription disagrees"):
        extract(load_batch(tmp_path, BATCH))


def test_an_unmapped_explicit_fact_is_not_silently_dropped(tmp_path: Path) -> None:
    fact = {"attribute": "new_kind_of_term", "raw_value": "1", "fact_status": "source_explicit", "evidence_ids": ["ev-notice"]}
    _write_batch(tmp_path, extra_fact=fact)

    with pytest.raises(SupplementError, match="unmapped explicit source fact"):
        extract(load_batch(tmp_path, BATCH))


def test_a_transcription_citing_an_empty_response_cannot_be_checked(tmp_path: Path) -> None:
    root = _write_batch(tmp_path)
    # Empty exDailyQ replies can carry a different, older field list; it must not be read as a schema.
    empty = _dump({"stat": "ok", "tables": [{"fields": EXDAILYQ_FIELDS[:5], "data": []}]})
    (root / "captured/tpex/exdailyq-3205.json").write_bytes(empty)
    manifest = json.loads((root / "captured/tpex/exdailyq-3205.manifest.json").read_bytes())
    manifest["sha256"] = hashlib.sha256(empty).hexdigest()
    manifest_body = _dump(manifest)
    (root / "captured/tpex/exdailyq-3205.manifest.json").write_bytes(manifest_body)
    _rewrite_identity(root, "captured/tpex/exdailyq-3205.json", empty, candidate_paths={"tpex/exdailyq-3205.json"})
    _rewrite_identity(root, "captured/tpex/exdailyq-3205.manifest.json", manifest_body)

    # The empty response is skipped, not rejected; with no rows, the 3205 cash transcription citing it cannot be cross-checked.
    with pytest.raises(SupplementError, match="mixes parsed and unparsed"):
        extract(load_batch(tmp_path, BATCH))


def test_twse_detail_fields_are_read_by_name_in_any_order(tmp_path: Path) -> None:
    _write_batch(tmp_path, twse_reversed_fields=True)
    db = _database(tmp_path)
    _import(tmp_path, db)

    twse = _terms(db, "1519")
    assert twse[("twse_twt49u_detail", "cash_dividend_per_share")][:3] == ("6", "NTD_per_share", "stated")
    assert twse[("twse_twt49u_detail", "free_shares_per_1000")][0] == "100"


def test_twse_detail_status_is_case_insensitive_like_the_capture_tool(tmp_path: Path) -> None:
    _write_batch(tmp_path, twse_stat="OK")
    db = _database(tmp_path)
    _import(tmp_path, db)

    assert _terms(db, "1519")[("twse_twt49u_detail", "cash_dividend_per_share")][0] == "6"


@pytest.mark.parametrize(
    ("local", "inside"),
    [
        ("2026-10-19T18:00", True),  # Monday
        ("2026-10-19T18:45", True),
        ("2026-10-19T18:46", False),
        ("2026-10-19T17:59", False),
        ("2026-10-18T18:10", False),  # Sunday
    ],
)
def test_producer_write_window_is_evaluated_in_taipei(local: str, inside: bool) -> None:
    taipei = datetime.fromisoformat(local).replace(tzinfo=timezone(timedelta(hours=8)))
    assert in_producer_write_window(taipei.astimezone(timezone.utc)) is inside


def test_cli_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_batch(tmp_path)
    db = _database(tmp_path)

    assert cli.main(["--batch", BATCH, "--data-dir", str(tmp_path), "--dry-run"]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["interruptions"] == 3
    with closing(sqlite3.connect(db)) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "corporate_action_terms" not in names


def test_cli_refuses_a_missing_database(tmp_path: Path) -> None:
    _write_batch(tmp_path)

    assert cli.main(["--batch", BATCH, "--data-dir", str(tmp_path), "--db", str(tmp_path / "absent.sqlite")]) in {2, 3}
    assert not (tmp_path / "absent.sqlite").exists()
