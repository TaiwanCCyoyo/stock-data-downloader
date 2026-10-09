"""Pure projection of hash-bound ordinary MOPS share-rights statements."""

from __future__ import annotations

import re
from decimal import Decimal
from fractions import Fraction
from typing import Any

from shp_utils import mops_dividend_statements as statements

_ORDINARY_PREFIX = "※除權--普通股："
_EMPTY_PREFERRED_SUFFIX = (
    "上市/櫃特別股(代碼：)：每壹股配發股票股利元（即每壹仟股無償配發普通股股），每壹仟股以每股新台幣元認購 特別股 股)，特別股股東配發普通股股數計有股。"
)
_NUMBER = r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
_ORDINARY_LINE = re.compile(
    rf"^※除權--普通股：每壹股配發股票\(股利\)(?P<money>{_NUMBER})?元"
    rf"（即每壹仟股無償配發盈餘轉增資(?P<earnings>{_NUMBER})?股，"
    rf"每壹仟股無償配發法定盈餘公積、資本公積轉增資(?P<reserves>{_NUMBER})?股），"
    rf"每壹仟股以每股新台幣(?P<price>{_NUMBER})?元認購 "
    rf"(?P<share_class>普通股|特別股) (?P<subscription>{_NUMBER})?股$"
)


def project_rights_statement(
    detail_body: bytes,
    *,
    company_id: str,
    announcement: dict[str, Any],
    expected_sha256: str,
) -> dict[str, Any]:
    """Project explicitly labeled ordinary rights without inferring delivery or selection."""
    context = statements.project_statement(
        detail_body,
        company_id=company_id,
        announcement=announcement,
        expected_sha256=expected_sha256,
    )
    context["schema_version"] = "mops-rights-statement.v1"
    context["rights_fields"] = _rights_fields(context["raw_blocks"]["rights_distribution"])
    return context


def _rights_fields(rights: str | None) -> dict[str, dict[str, Any]]:
    lines = [] if rights is None else [line for line in rights.splitlines() if line.lstrip().startswith(_ORDINARY_PREFIX)]
    if not lines:
        return _unknown_fields("missing", None)
    if len(lines) != 1:
        return _unknown_fields("ambiguous", lines)
    line = lines[0]
    # The held 2019 form appends this exact empty preferred-share template.
    # Do not trim nonempty templates or arbitrary suffixes; retain full raw text.
    match = _ORDINARY_LINE.fullmatch(line.lstrip().removesuffix(_EMPTY_PREFERRED_SUFFIX))
    if match is None:
        return _unknown_fields("unsupported", line)

    values = match.groupdict()
    fields = {
        "stock_dividend_money_per_share": _numeric(values["money"], line),
        "earnings_free_per_thousand": _numeric(values["earnings"], line),
        "reserves_free_per_thousand": _numeric(values["reserves"], line),
        "subscription_price": _numeric(values["price"], line),
        "subscription_per_thousand": _numeric(values["subscription"], line),
        "subscription_class": {"value": values["share_class"], "status": "stated", "raw": line},
    }
    fields["bonus_per_share"] = _bonus(fields["earnings_free_per_thousand"], fields["reserves_free_per_thousand"], line)
    return fields


def _numeric(value: str | None, raw: str) -> dict[str, Any]:
    return {"value": value, "status": "stated" if value is not None else "blank", "raw": raw}


def _bonus(earnings: dict[str, Any], reserves: dict[str, Any], raw: str) -> dict[str, Any]:
    if earnings["status"] != "stated" or reserves["status"] != "stated":
        return {"value": None, "status": "blank", "raw": raw}
    ratio = (Fraction(Decimal(earnings["value"])) + Fraction(Decimal(reserves["value"]))) / 1000
    return {"value": {"numerator": ratio.numerator, "denominator": ratio.denominator}, "status": "stated", "raw": raw}


def _unknown_fields(status: str, raw: str | list[str] | None) -> dict[str, dict[str, Any]]:
    names = (
        "stock_dividend_money_per_share",
        "earnings_free_per_thousand",
        "reserves_free_per_thousand",
        "subscription_price",
        "subscription_per_thousand",
        "subscription_class",
        "bonus_per_share",
    )
    return {name: {"value": None, "status": status, "raw": raw} for name in names}
