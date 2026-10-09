"""Pure offline contracts for ordinary MOPS dividend statement projection."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from shp_utils import mops_dividend_evidence as evidence
from shp_utils import mops_dividend_statements as statements


def _announcement() -> dict[str, Any]:
    return {
        "detail_api_name": "t108sb19_detail",
        "detail_parameters": {
            "declarationDate": "1130715",
            "etn_no": "",
            "companyId": "6509",
            "serialNumber": 2,
            "detailReportKind": "A",
        },
        "subject_raw": "Ordinary dividend announcement",
        "displayed_announcement_date_raw": "113/07/15",
        "revised_payment_date_raw": "無",
    }


def _body(
    *,
    rights: Any = (
        "※除息--普通股：每壹股配發現金(股利)1.25000000元，distribution\n"
        "＊股利所屬年度：112年，股利所屬期間第3季\n"
        "＊現金股利發放日：113年8月21日（發放現金股利時適用。）"
    ),
    record: Any = "113年7月31日\n除權/除息交易日：113年7月25日",
    foreign: Any = "否",
    subject: str = "Ordinary dividend announcement",
    serial: int = 2,
) -> bytes:
    titles = ["一、公告序號：", "二、主旨：", "（五）權利分派內容：", "（六）現金股利之一部或全部是否以外幣發放：", "（八）權利分派基準日："]
    return json.dumps(
        {
            "code": 200,
            "result": {
                "companyId": "6509",
                "titles": [{"main": title, "sub": []} for title in titles],
                "data": [[serial, subject, rights, foreign, record]],
            },
        },
        ensure_ascii=False,
    ).encode()


def _project(body: bytes, announcement: dict[str, Any] | None = None) -> dict[str, Any]:
    return statements.project_statement(
        body,
        company_id="6509",
        announcement=announcement or _announcement(),
        expected_sha256=hashlib.sha256(body).hexdigest(),
    )


def test_projects_exact_decimal_period_dates_and_raw_named_blocks():
    body = _body()
    record = _project(body)

    assert record["schema_version"] == "mops-dividend-statement.v1"
    assert record["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert record["response_revision_id"] == record["body_sha256"]
    assert record["fields"] == {
        "cash_per_share": {
            "value": "1.25000000",
            "status": "stated",
            "raw": "※除息--普通股：每壹股配發現金(股利)1.25000000元，distribution",
        },
        "dividend_period": {
            "value": {"roc_year": 112, "period": "Q3"},
            "status": "stated",
            "raw": "＊股利所屬年度：112年，股利所屬期間第3季",
        },
        "record_date": {"value": "2024-07-31", "status": "stated", "raw": "113年7月31日"},
        "stated_ex_date": {"value": "2024-07-25", "status": "stated", "raw": "除權/除息交易日：113年7月25日"},
        "announced_payment_date": {
            "value": "2024-08-21",
            "status": "stated",
            "raw": "＊現金股利發放日：113年8月21日（發放現金股利時適用。）",
        },
    }
    assert record["raw_blocks"]["foreign_currency_answer"] == "否"
    assert record["payment_resolution"] == "unresolved"
    assert record["event_link_status"] == "unlinked"
    assert "cash_event" not in record


def test_explicit_zero_remains_stated_and_blank_template_stays_blank():
    rights = "\n".join([
        "※除息--普通股：每壹股配發現金(股利)0.00000000元，distribution",
        "＊股利所屬年度：其他　不適用",
        "＊現金股利發放日：　年　月　日（發放現金股利時適用。）",
    ])
    record = _project(_body(rights=rights))
    assert record["fields"]["cash_per_share"]["value"] == "0.00000000"
    assert record["fields"]["cash_per_share"]["status"] == "stated"
    assert record["fields"]["dividend_period"] == {
        "value": None,
        "status": "not_applicable",
        "raw": "＊股利所屬年度：其他　不適用",
    }
    assert record["fields"]["announced_payment_date"]["status"] == "blank"
    assert record["fields"]["announced_payment_date"]["value"] is None


def test_whitespace_only_labelled_period_ex_date_and_payment_are_blank():
    rights = "\n".join([
        "＊股利所屬年度：   ",
        "＊現金股利發放日：   （發放現金股利時適用。）",
    ])
    record = _project(_body(rights=rights, record="113年7月31日\n除權/除息交易日：   "))
    assert record["fields"]["dividend_period"]["status"] == "blank"
    assert record["fields"]["stated_ex_date"]["status"] == "blank"
    assert record["fields"]["announced_payment_date"]["status"] == "blank"


@pytest.mark.parametrize("suffix", ["   ", "\t元", "\t元，distribution"])
def test_cash_whitespace_placeholders_are_blank(suffix: str):
    record = _project(_body(rights=f"※除息--普通股：每壹股配發現金(股利){suffix}"))
    assert record["fields"]["cash_per_share"]["status"] == "blank"
    assert record["fields"]["cash_per_share"]["value"] is None


def test_nonblank_malformed_cash_suffix_is_unsupported():
    record = _project(_body(rights="※除息--普通股：每壹股配發現金(股利)元unexpected"))
    assert record["fields"]["cash_per_share"]["status"] == "unsupported"
    assert record["fields"]["cash_per_share"]["value"] is None


def test_multiple_unlabelled_record_date_candidates_are_ambiguous():
    record = _project(_body(record="113年7月31日\n113年8月1日\n除權/除息交易日：113年7月25日"))
    field = record["fields"]["record_date"]
    assert field["value"] is None
    assert field["status"] == "ambiguous"
    assert field["raw"] == ["113年7月31日", "113年8月1日"]


def test_ex_date_alone_does_not_become_record_date():
    record = _project(_body(record="除權/除息交易日：113年7月25日"))
    assert record["fields"]["record_date"] == {"value": None, "status": "missing", "raw": None}
    assert record["fields"]["stated_ex_date"]["value"] == "2024-07-25"


def test_reordered_titles_preserve_values_and_source_body_identity():
    packet = json.loads(_body())
    packet["result"]["titles"].reverse()
    packet["result"]["data"][0].reverse()
    changed = _project(json.dumps(packet).encode())
    original = _project(_body())
    assert changed["fields"] == original["fields"]
    assert changed["announcement_id"] == original["announcement_id"]
    assert changed["response_revision_id"] != original["response_revision_id"]


def test_same_terms_in_different_announcements_are_not_one_statement():
    announcement = _announcement()
    first = _project(_body(), announcement)
    announcement["detail_parameters"]["declarationDate"] = "1130716"
    second = _project(_body(), announcement)
    assert first["fields"] == second["fields"]
    assert first["announcement_id"] != second["announcement_id"]
    assert first["event_link_status"] == second["event_link_status"] == "unlinked"


@pytest.mark.parametrize("date_text", ["113年2月30日", "000年1月1日", "113年8月21日，受影響地區另議"])
def test_invalid_or_qualified_payment_date_is_not_stated(date_text: str):
    record = _project(_body(rights=f"＊現金股利發放日：{date_text}（發放現金股利時適用。）"))
    assert record["fields"]["announced_payment_date"]["status"] == "unsupported"
    assert record["fields"]["announced_payment_date"]["value"] is None


def test_absent_period_and_blocks_remain_missing_without_announcement_year_inference():
    rights = "※除息--普通股：每壹股配發現金(股利)0.60000000元，distribution"
    packet = json.loads(_body(rights=rights))
    packet["result"]["titles"] = packet["result"]["titles"][:3]
    packet["result"]["data"][0] = packet["result"]["data"][0][:3]
    body = json.dumps(packet, ensure_ascii=False).encode()
    record = _project(body)
    assert record["fields"]["dividend_period"] == {"value": None, "status": "missing", "raw": None}
    assert record["fields"]["record_date"] == {"value": None, "status": "missing", "raw": None}
    assert record["fields"]["announced_payment_date"] == {
        "value": None,
        "status": "missing",
        "raw": None,
    }
    assert record["raw_blocks"]["foreign_currency_answer"] is None


@pytest.mark.parametrize(
    "rights,field,status",
    [
        ("※除息--普通股：每壹股配發現金(股利)one元，distribution", "cash_per_share", "unsupported"),
        ("※除息--普通股：每壹股配發現金(股利)1元，x\n※除息--普通股：每壹股配發現金(股利)1元，x", "cash_per_share", "ambiguous"),
        ("＊股利所屬年度：112年，股利所屬期間第5季", "dividend_period", "unsupported"),
        ("＊現金股利發放日：113年8月21日 conditional", "announced_payment_date", "unsupported"),
    ],
)
def test_unsupported_or_ambiguous_labels_remain_nonstated(rights: str, field: str, status: str):
    result = _project(_body(rights=rights))
    assert result["fields"][field]["value"] is None
    assert result["fields"][field]["status"] == status


@pytest.mark.parametrize(
    "body,company,announcement,expected_hash",
    [
        (b"{}", "6509", _announcement(), "0" * 64),
        (_body(), "bad", _announcement(), hashlib.sha256(_body()).hexdigest()),
        (_body(), "6509", {"subject_raw": "missing"}, hashlib.sha256(_body()).hexdigest()),
    ],
)
def test_rejects_invalid_hash_company_or_announcement_context(
    body: bytes,
    company: str,
    announcement: dict[str, Any],
    expected_hash: str,
):
    with pytest.raises(evidence.EvidenceError):
        statements.project_statement(
            body,
            company_id=company,
            announcement=announcement,
            expected_sha256=expected_hash,
        )


@pytest.mark.parametrize("body", ["not bytes", b"{" + b"a" * statements.MAX_BODY_BYTES], ids=["wrong-type", "oversized"])
def test_rejects_invalid_body_type_or_size(body: Any):
    with pytest.raises(evidence.EvidenceError):
        statements.project_statement(
            body,
            company_id="6509",
            announcement=_announcement(),
            expected_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda packet: packet["result"]["data"][0].__setitem__(0, 9),
        lambda packet: packet["result"].__setitem__("companyId", "9999"),
        lambda packet: packet["result"]["data"][0].__setitem__(1, "different"),
        lambda packet: packet["result"]["titles"].append({"main": "（五）權利分派內容：", "sub": []}),
    ],
)
def test_rejects_detail_identity_or_structural_ambiguity(mutate: Any):
    packet = json.loads(_body())
    mutate(packet)
    body = json.dumps(packet, ensure_ascii=False).encode()
    with pytest.raises(evidence.EvidenceError):
        _project(body)


def test_rejects_nonstring_required_block_and_keeps_output_isolated_from_caller_mutation():
    packet = json.loads(_body())
    packet["result"]["data"][0][2] = 3
    body = json.dumps(packet, ensure_ascii=False).encode()
    with pytest.raises(evidence.EvidenceError):
        _project(body)

    announcement = _announcement()
    result = _project(_body(), announcement)
    announcement["detail_parameters"]["serialNumber"] = 99
    announcement["subject_raw"] = "mutated"
    assert result["detail_parameters"]["serialNumber"] == 2
    assert result["source_subject_raw"] == "Ordinary dividend announcement"
