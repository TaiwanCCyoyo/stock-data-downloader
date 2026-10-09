from __future__ import annotations

import hashlib
import json
from decimal import localcontext
from typing import Any

import pytest

from shp_utils import mops_dividend_evidence as evidence
from shp_utils.mops_rights_evidence import _EMPTY_PREFERRED_SUFFIX, project_rights_statement


def announcement() -> dict[str, Any]:
    return {
        "detail_api_name": "t108sb19_detail",
        "detail_parameters": {"declarationDate": "1120711", "etn_no": "", "companyId": "3092", "serialNumber": 1, "detailReportKind": "A"},
        "subject_raw": "Ordinary rights announcement",
        "displayed_announcement_date_raw": "112/07/11",
        "revised_payment_date_raw": "無",
    }


def ordinary_line(
    earnings: str = "30.00000000",
    reserves: str = "20.00000000",
    *,
    share_class: str = "普通股",
    subscription: str = "100.00000000",
) -> str:
    return (
        f"※除權--普通股：每壹股配發股票(股利)0.05000000元（即每壹仟股無償配發盈餘轉增資{earnings}股，"
        f"每壹仟股無償配發法定盈餘公積、資本公積轉增資{reserves}股），每壹仟股以每股新台幣18.50元認購 {share_class} {subscription}股"
    )


def body(rights: Any = None) -> bytes:
    rights = ordinary_line() if rights is None else rights
    titles = ["一、公告序號：", "二、主旨：", "（五）權利分派內容："]
    packet = {
        "code": 200,
        "result": {"companyId": "3092", "titles": [{"main": title, "sub": []} for title in titles], "data": [[1, "Ordinary rights announcement", rights]]},
    }
    return json.dumps(packet, ensure_ascii=False).encode()


def project(detail_body: bytes, **kwargs: Any) -> dict[str, Any]:
    return project_rights_statement(
        detail_body,
        company_id=kwargs.pop("company_id", "3092"),
        announcement=kwargs.pop("announcement", announcement()),
        expected_sha256=kwargs.pop("expected_sha256", hashlib.sha256(detail_body).hexdigest()),
        **kwargs,
    )


def test_projects_exact_free_ratio_and_keeps_paid_terms_separate() -> None:
    result = project(body())
    fields = result["rights_fields"]
    assert result["schema_version"] == "mops-rights-statement.v1"
    assert fields["bonus_per_share"]["value"] == {"numerator": 1, "denominator": 20}
    assert fields["subscription_per_thousand"]["value"] == "100.00000000"
    assert fields["subscription_price"]["value"] == "18.50"
    assert result["historical_public_availability"] == result["revision_completeness"] == "unverified"


def test_zero_is_stated_not_missing_or_blank() -> None:
    fields = project(body(ordinary_line("0", "0", subscription="0")))["rights_fields"]
    assert fields["earnings_free_per_thousand"]["status"] == "stated"
    assert fields["bonus_per_share"] == {"value": {"numerator": 0, "denominator": 1}, "status": "stated", "raw": ordinary_line("0", "0", subscription="0")}


def test_fraction_arithmetic_does_not_round_at_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 2
        fields = project(body(ordinary_line("30.12345678", "0.00000001")))["rights_fields"]
    assert fields["bonus_per_share"]["value"] == {"numerator": 3012345679, "denominator": 100000000000}


def test_preferred_subscription_stays_paid_and_not_free_delivery() -> None:
    fields = project(body(ordinary_line("30", "0", share_class="特別股")))["rights_fields"]
    assert fields["subscription_class"]["value"] == "特別股"
    assert fields["bonus_per_share"]["value"] == {"numerator": 3, "denominator": 100}


@pytest.mark.parametrize(
    "rights,status,raw_type",
    [
        ("※除權--特別股：每壹股配發股票(股利)99元", "missing", type(None)),
        (ordinary_line() + "\n" + ordinary_line(), "ambiguous", list),
        ("※除權--普通股：每壹股配發股票(股利)30元 text", "unsupported", str),
        (
            "※除權--普通股：每壹股配發股票(股利)元（即每壹仟股無償配發盈餘轉增資股，"
            "每壹仟股無償配發法定盈餘公積、資本公積轉增資股），每壹仟股以每股新台幣元認購 普通股 股",
            "blank",
            str,
        ),
    ],
)
def test_missing_duplicate_unsupported_and_blank_remain_explicit(rights: str, status: str, raw_type: type) -> None:
    fields = project(body(rights))["rights_fields"]
    assert fields["bonus_per_share"]["status"] == status
    assert isinstance(fields["bonus_per_share"]["raw"], raw_type)
    assert fields["bonus_per_share"]["value"] is None


def test_blank_preferred_template_suffix_is_unsupported_not_zero() -> None:
    template = (
        "※除權--普通股：每壹股配發股票(股利)元（即每壹仟股無償配發盈餘轉增資股，"
        "每壹仟股無償配發法定盈餘公積、資本公積轉增資股），每壹仟股以每股新台幣元認購 特別股 股（特別股）"
    )
    fields = project(body(template))["rights_fields"]
    assert fields["earnings_free_per_thousand"] == {"value": None, "status": "unsupported", "raw": template}


def test_observed_empty_preferred_suffix_retains_ordinary_terms_and_full_raw() -> None:
    line = ordinary_line() + _EMPTY_PREFERRED_SUFFIX
    fields = project(body(line))["rights_fields"]
    assert fields["bonus_per_share"]["value"] == {"numerator": 1, "denominator": 20}
    assert fields["bonus_per_share"]["raw"] == line
    blank = ordinary_line("", "") + _EMPTY_PREFERRED_SUFFIX
    assert project(body(blank))["rights_fields"]["bonus_per_share"]["status"] == "blank"


@pytest.mark.parametrize(
    "suffix",
    [
        _EMPTY_PREFERRED_SUFFIX.replace("代碼：)", "代碼：1101B)"),
        _EMPTY_PREFERRED_SUFFIX + _EMPTY_PREFERRED_SUFFIX,
        _EMPTY_PREFERRED_SUFFIX + "arbitrary",
    ],
)
def test_nonempty_or_extra_preferred_suffix_remains_unresolved(suffix: str) -> None:
    fields = project(body(ordinary_line() + suffix))["rights_fields"]
    assert fields["bonus_per_share"]["status"] == "unsupported"
    assert fields["bonus_per_share"]["value"] is None


@pytest.mark.parametrize("mutation", [lambda packet: packet["result"].__setitem__("companyId", "9999"), lambda packet: packet.__setitem__("code", 500)])
def test_source_binding_failures_use_existing_statement_boundary(mutation: Any) -> None:
    packet = json.loads(body())
    mutation(packet)
    changed = json.dumps(packet, ensure_ascii=False).encode()
    with pytest.raises(evidence.EvidenceError):
        project(changed)


def test_hash_and_announcement_mismatch_reject_before_rights_parse() -> None:
    with pytest.raises(evidence.EvidenceError, match="hash"):
        project(body(), expected_sha256="0" * 64)
    invalid = announcement()
    invalid["subject_raw"] = "different"
    with pytest.raises(evidence.EvidenceError, match="subject"):
        project(body(), announcement=invalid)


def test_cash_fields_and_raw_context_are_not_reinterpreted() -> None:
    rights = ordinary_line() + "\n※除息--普通股：每壹股配發現金(股利)5元，cash"
    result = project(body(rights))
    assert result["fields"]["cash_per_share"]["value"] == "5"
    assert result["rights_fields"]["stock_dividend_money_per_share"]["value"] == "0.05000000"
    assert result["payment_resolution"] == "unresolved" and result["event_link_status"] == "unlinked"
