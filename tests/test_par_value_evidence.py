"""Self-authored synthetic identities/prices with provider interface field labels."""

from __future__ import annotations

import json

import pytest

from shp_utils.par_value_evidence import ParValueEvidenceError, extract_par_value_resumes, extract_twse_par_detail

TWSE_FIELDS = ["恢復買賣日期", "股票代號", "停止買賣前收盤價格", "恢復買賣參考價", "開盤競價基準", "詳細資料"]
DETAIL_FIELDS = ["股票代號", "停止買賣日期", "每股換發新股票", "變更前面額", "變更後面額"]
TPEX_FIELDS = ["恢復買賣日期", "證券代號", "最後交易日之收盤價格", "恢復買賣開始參考價", "開始交易基準價", "詳細資料"]


def twse_resume(*, code: str = "9001", resume: str = "111/07/13", stop: str = "20220706") -> bytes:
    row = [resume, code, "200.00", "50.00", "50.02", f"{code},{stop},20220713"]
    return json.dumps({"stat": "OK", "fields": TWSE_FIELDS, "data": [row]}, ensure_ascii=False).encode()


def twse_detail(*, code: str = "9001", stop: str = "111/07/06", rows: list[list[str]] | None = None) -> bytes:
    data = rows if rows is not None else [[code, "name", stop, "4.00000000 股", "10.00 元/股", "2.50 元/股"]]
    fields = ["股票代號", "名稱", *DETAIL_FIELDS[1:]]
    return json.dumps({"stat": "OK", "fields": fields, "data": data}, ensure_ascii=False).encode()


def tpex_detail(*, code: str = "9002", stop: str = "111/12/01", resume: str = "111/12/12", ratio: str = "4.00000000") -> str:
    pairs = [
        ("證券代號/證券名稱:", f"{code} / name"),
        ("停止買賣日期:", stop),
        ("恢復買賣日期:", resume),
        ("變更股票面額換股率:", ratio),
        ("變更前股票面額:", "10.00"),
        ("變更後股票面額:", "2.50"),
    ]
    return "<table>" + "".join(f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in pairs) + "</table>"


def tpex_resume(*, detail: str | None = None, count: int = 1) -> bytes:
    row = ["1111212", "9002", "100.01", "25.00", "25.02", detail or tpex_detail()]
    packet = {"stat": "ok", "tables": [{"totalCount": count, "fields": TPEX_FIELDS, "data": [row]}]}
    return json.dumps(packet, ensure_ascii=False).encode()


def test_tpex_embedded_exact_ratio_keeps_rounded_reference_and_auction_separate() -> None:
    result = extract_par_value_resumes(tpex_resume(), source="tpex/pvChgRslt", start="2022-01-01", end="2022-12-31")
    row = result["records"][0]
    assert result["status"] == "observed" and row["market"] == "TPEx"
    assert row["exchange_ratio"] == {"numerator": 4, "denominator": 1}
    assert (row["previous_close"], row["resume_reference_price"], row["opening_auction_base"]) == ("100.01", "25.00", "25.02")
    assert row["historical_available_at"] is None


def test_twse_resume_is_unresolved_until_bound_detail_supplies_exact_terms() -> None:
    resume = twse_resume()
    row = extract_par_value_resumes(resume, source="twse/twtb8u", start="2022-01-01", end="2022-12-31")["records"][0]
    assert row["exchange_ratio"] is None and row["exchange_ratio_status"] == "unresolved"
    assert row["previous_close"] == "200.00"
    bound = extract_twse_par_detail(twse_detail(), resume_body=resume, resume_row_index=0, start="2022-01-01", end="2022-12-31")["records"][0]
    assert bound["exchange_ratio"] == {"numerator": 4, "denominator": 1}
    assert bound["old_face_value"] == "10.00" and bound["new_face_value"] == "2.50"
    assert bound["detail_literal_fields"]["每股換發新股票"] == "4.00000000 股"


def test_empty_detail_keeps_matched_resume_event_unresolved() -> None:
    result = extract_twse_par_detail(twse_detail(rows=[]), resume_body=twse_resume(), resume_row_index=0, start="2022-01-01", end="2022-12-31")
    row = result["records"][0]
    assert result["status"] == "observed"
    assert row["exchange_ratio"] is None and row["detail_literal_fields"] is None


def test_missing_detail_quantities_remain_unresolved_without_deriving_face_ratio() -> None:
    rows = [["9001", "name", "111/07/06", "--", "", "--"]]
    row = extract_twse_par_detail(twse_detail(rows=rows), resume_body=twse_resume(), resume_row_index=0, start="2022-01-01", end="2022-12-31")["records"][0]
    assert row["exchange_ratio"] is None and row["exchange_ratio_status"] == "unresolved"
    assert row["old_face_value"] is row["new_face_value"] is None


@pytest.mark.parametrize(
    "detail",
    [
        twse_detail(code="9999"),
        twse_detail(stop="111/07/07"),
        twse_detail(rows=[["9001", "name", "111/07/06", "3.00000000 股", "10.00 元/股", "2.50 元/股"]]),
    ],
)
def test_twse_detail_identity_and_face_ratio_conflicts_reject(detail: bytes) -> None:
    with pytest.raises(ParValueEvidenceError):
        extract_twse_par_detail(detail, resume_body=twse_resume(), resume_row_index=0, start="2022-01-01", end="2022-12-31")


def test_tpex_declared_count_duplicate_and_out_of_window_reject() -> None:
    with pytest.raises(ParValueEvidenceError, match="count"):
        extract_par_value_resumes(tpex_resume(count=2), source="tpex/pvChgRslt", start="2022-01-01", end="2022-12-31")
    packet = json.loads(tpex_resume())
    packet["tables"][0]["data"].append(packet["tables"][0]["data"][0].copy())
    packet["tables"][0]["totalCount"] = 2
    with pytest.raises(ParValueEvidenceError, match="duplicate"):
        extract_par_value_resumes(json.dumps(packet, ensure_ascii=False).encode(), source="tpex/pvChgRslt", start="2022-01-01", end="2022-12-31")
    with pytest.raises(ParValueEvidenceError, match="outside"):
        extract_par_value_resumes(tpex_resume(), source="tpex/pvChgRslt", start="2023-01-01", end="2023-12-31")


def test_resume_window_allows_earlier_stop_but_rejects_reversed_dates_and_zero_prices() -> None:
    packet = json.loads(twse_resume())
    packet["data"][0][0] = "111/01/03"
    packet["data"][0][-1] = "9001,20211225,20220103"
    result = extract_par_value_resumes(json.dumps(packet, ensure_ascii=False).encode(), source="twse/twtb8u", start="2022-01-01", end="2022-01-31")
    assert result["records"][0]["stop_date"] == "2021-12-25"
    packet["data"][0][-1] = "9001,20220104,20220103"
    with pytest.raises(ParValueEvidenceError, match="precede"):
        extract_par_value_resumes(json.dumps(packet, ensure_ascii=False).encode(), source="twse/twtb8u", start="2022-01-01", end="2022-01-31")
    packet = json.loads(twse_resume())
    packet["data"][0][2] = "0"
    with pytest.raises(ParValueEvidenceError, match="numeric"):
        extract_par_value_resumes(json.dumps(packet, ensure_ascii=False).encode(), source="twse/twtb8u", start="2022-01-01", end="2022-12-31")


def test_inert_html_requires_unique_complete_matching_labels() -> None:
    duplicate = tpex_detail().replace("</table>", "<tr><th>停止買賣日期:</th><td>111/12/01</td></tr></table>")
    with pytest.raises(ParValueEvidenceError, match="labels"):
        extract_par_value_resumes(tpex_resume(detail=duplicate), source="tpex/pvChgRslt", start="2022-01-01", end="2022-12-31")
    wrong_code = tpex_detail(code="9999")
    with pytest.raises(ParValueEvidenceError, match="code"):
        extract_par_value_resumes(tpex_resume(detail=wrong_code), source="tpex/pvChgRslt", start="2022-01-01", end="2022-12-31")
    orphan = "<table><tr><th>證券代號/證券名稱:</th><th>停止買賣日期:</th><td>111/12/01</td></tr></table>"
    with pytest.raises(ParValueEvidenceError):
        extract_par_value_resumes(tpex_resume(detail=orphan), source="tpex/pvChgRslt", start="2022-01-01", end="2022-12-31")


def test_empty_valid_resume_is_unobserved_not_complete_coverage() -> None:
    packet = json.loads(twse_resume())
    packet["data"] = []
    result = extract_par_value_resumes(json.dumps(packet, ensure_ascii=False).encode(), source="twse/twtb8u", start="2022-01-01", end="2022-12-31")
    assert result["status"] == "unobserved_empty" and result["records"] == []
