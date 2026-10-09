"""Pytest coverage for shp_utils/official_api.py.

Run with: uv run pytest test_official_api.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
import requests

from shp_utils import official_api
from shp_utils.official_api import (
    OfficialFetchError,
    _retry_delay,
    fetch_bytes_with_retry_url,
    fetch_json_with_retry,
    fetch_json_with_retry_url,
    parse_int,
    parse_number,
    parse_roc_compact_date,
)


def test_parse_roc_compact_date_converts_seven_digit_roc_date_to_iso():
    assert parse_roc_compact_date("1150722") == "2026-07-22"


def test_parse_roc_compact_date_returns_none_for_wrong_length():
    assert parse_roc_compact_date("150722") is None


def test_parse_roc_compact_date_returns_none_for_non_digit_value():
    assert parse_roc_compact_date("abcdefg") is None


def test_parse_number_strips_commas():
    assert parse_number("37,544,470") == 37544470.0


def test_parse_number_strips_surrounding_whitespace():
    assert parse_number(" 43.05") == 43.05


def test_parse_number_returns_none_for_dash_sentinel():
    assert parse_number("---") is None
    assert parse_number(" ---") is None


def test_parse_number_returns_none_for_none_value():
    assert parse_number(None) is None


def test_parse_number_returns_zero_for_literal_zero():
    assert parse_number("0.00") == 0.0


def test_parse_int_truncates_numeric_string():
    assert parse_int("31653123") == 31653123


def test_parse_int_returns_none_for_dash_sentinel():
    assert parse_int("---") is None


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: Any = None,
        text: str = "",
        content: bytes = b"",
        url: str = "http://example.test",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.text = text
        self.content = content
        self.url = url
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self) -> Any:
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]


def test_fetch_json_with_retry_returns_parsed_json_on_success(monkeypatch: pytest.MonkeyPatch):
    calls = []

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(json_body=[{"a": 1}])

    monkeypatch.setattr(requests, "get", fake_get)
    result = fetch_json_with_retry("http://example.test/data")
    assert result == [{"a": 1}]
    assert calls == ["http://example.test/data"]


def test_fetch_json_with_retry_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    attempts = {"count": 0}

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return _FakeResponse(status_code=500, text="server error")
        return _FakeResponse(json_body=[{"ok": True}])

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    result = fetch_json_with_retry("http://example.test/data", retries=3, retry_sleep=0.01)
    assert result == [{"ok": True}]
    assert attempts["count"] == 3


def test_fetch_json_with_retry_raises_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        return _FakeResponse(status_code=500, text="server error")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError):
        fetch_json_with_retry("http://example.test/data", retries=1, retry_sleep=0.01)


def test_fetch_json_with_retry_retries_a_connection_failure_then_succeeds(monkeypatch: pytest.MonkeyPatch):
    # The retries exist for exactly this: a transport failure with no response
    # at all. They used to be unreachable because the GET sat outside the
    # retried block, so one TWSE read timeout aborted a whole pipeline step.
    attempts = {"count": 0}

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.ReadTimeout("read timed out")
        return _FakeResponse(json_body=[{"ok": True}])

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    result = fetch_json_with_retry("http://example.test/data", retries=3, retry_sleep=0.01)
    assert result == [{"ok": True}]
    assert attempts["count"] == 3


def test_fetch_json_with_retry_raises_runtime_error_when_every_request_fails(monkeypatch: pytest.MonkeyPatch):
    attempts = {"count": 0}

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        attempts["count"] += 1
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="no response"):
        fetch_json_with_retry("http://example.test/data", retries=2, retry_sleep=0.01)
    assert attempts["count"] == 3


def test_fetch_json_with_retry_url_returns_the_resolved_request_url(monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        return _FakeResponse(json_body={"stat": "OK"}, url="http://example.test/data?startDate=20260101")

    monkeypatch.setattr(requests, "get", fake_get)
    payload, resolved = fetch_json_with_retry_url("http://example.test/data", params={"startDate": "20260101"})
    assert payload == {"stat": "OK"}
    assert resolved == "http://example.test/data?startDate=20260101"


def test_fetch_bytes_with_retry_url_returns_bytes_and_resolved_url(monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None, stream: bool = False) -> _FakeResponse:
        return _FakeResponse(content=b"raw,csv\n", url="http://example.test/data.csv?month=202601")

    monkeypatch.setattr(requests, "get", fake_get)

    payload, resolved = fetch_bytes_with_retry_url("http://example.test/data.csv", params={"month": "202601"})

    assert payload == b"raw,csv\n"
    assert resolved == "http://example.test/data.csv?month=202601"


def test_fetch_bytes_with_retry_url_retries_transport_failure(monkeypatch: pytest.MonkeyPatch):
    attempts = {"count": 0}

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None, stream: bool = False) -> _FakeResponse:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise requests.ReadTimeout("read timed out")
        return _FakeResponse(content=b"ok")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    payload, _resolved = fetch_bytes_with_retry_url("http://example.test/data.csv", retries=1, retry_sleep=0.01)

    assert payload == b"ok"
    assert attempts["count"] == 2


def test_fetch_bytes_with_retry_url_reports_final_http_failure(monkeypatch: pytest.MonkeyPatch):
    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None, stream: bool = False) -> _FakeResponse:
        return _FakeResponse(status_code=503, text="service unavailable")

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="status=503"):
        fetch_bytes_with_retry_url("http://example.test/data.csv", retries=1, retry_sleep=0.01)


def test_fetch_bytes_with_retry_url_rejects_oversized_stream(monkeypatch: pytest.MonkeyPatch):
    attempts = 0

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None, stream: bool = False) -> _FakeResponse:
        nonlocal attempts
        attempts += 1
        return _FakeResponse(content=b"12345")

    monkeypatch.setattr(requests, "get", fake_get)

    with pytest.raises(RuntimeError, match="response exceeds"):
        fetch_bytes_with_retry_url("http://example.test/data.csv", retries=3, max_bytes=4)
    assert attempts == 1


def test_non_retryable_client_error_is_not_retried(monkeypatch: pytest.MonkeyPatch):
    """A 404 says the request was wrong; asking three more times cannot fix it,
    and on a rate-limited source it spends budget a transient failure will need."""
    attempts = 0

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        nonlocal attempts
        attempts += 1
        return _FakeResponse(status_code=404)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with pytest.raises(OfficialFetchError) as excinfo:
        fetch_json_with_retry("http://example.test/data", retries=3, retry_sleep=0.01)

    assert attempts == 1
    assert excinfo.value.status_code == 404


def test_rate_limited_status_is_retried_and_carries_its_code(monkeypatch: pytest.MonkeyPatch):
    attempts = 0

    def fake_get(url: str, params: Any = None, timeout: Any = None, headers: Any = None) -> _FakeResponse:
        nonlocal attempts
        attempts += 1
        return _FakeResponse(status_code=520)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    with pytest.raises(OfficialFetchError) as excinfo:
        fetch_json_with_retry("http://example.test/data", retries=2, retry_sleep=0.01)

    assert attempts == 3
    assert excinfo.value.status_code == 520
    assert excinfo.value.attempts == 3


def test_retry_delay_grows_exponentially_and_stays_under_the_cap():
    delays = [_retry_delay(None, attempt=attempt, retry_sleep=10.0, max_retry_sleep=100.0) for attempt in range(1, 6)]
    assert 10.0 <= delays[0] <= 12.5
    assert 20.0 <= delays[1] <= 25.0
    assert 40.0 <= delays[2] <= 50.0
    assert all(delay <= 125.0 for delay in delays)
    assert delays[4] >= 100.0


def test_retry_delay_honours_a_numeric_retry_after_header():
    response = _FakeResponse(status_code=429, headers={"Retry-After": "45"})
    delay = _retry_delay(cast(requests.Response, response), attempt=1, retry_sleep=1.0, max_retry_sleep=300.0)
    assert delay == 45.0


def test_retry_delay_ignores_an_unparsable_retry_after_header():
    response = _FakeResponse(status_code=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    delay = _retry_delay(cast(requests.Response, response), attempt=1, retry_sleep=8.0, max_retry_sleep=300.0)
    assert 8.0 <= delay <= 10.0


class _StubResponse:
    """Just enough of a requests.Response for the host check."""

    def __init__(self, url: str, requested: str = "https://www.twse.com.tw/rwd/zh/fund/T86"):
        self.url = url
        self.request = SimpleNamespace(url=requested)


@pytest.mark.parametrize(
    "final_url",
    [
        "https://mirror.example.net/rwd/zh/fund/T86",
        # Ends with the expected name and is a different party, which is why the
        # comparison is exact equality rather than a suffix test.
        "https://www.twse.com.tw.example.net/rwd/zh/fund/T86",
        "http://www.twse.com.tw/rwd/zh/fund/T86",
        "https://www.twse.com.tw:8443/rwd/zh/fund/T86",
        "https://user:pw@www.twse.com.tw/rwd/zh/fund/T86",  # pragma: allowlist secret
        "https://www.twse.com.tw/rwd/zh/fund/T86#frag",
    ],
)
def test_a_response_from_the_wrong_place_is_rejected(final_url: str) -> None:
    with pytest.raises(official_api.UnexpectedResponseHostError) as excinfo:
        official_api._require_response_host(cast(requests.Response, _StubResponse(final_url)), expected_host="www.twse.com.tw")
    assert excinfo.value.final_url == final_url


def test_the_expected_host_passes_regardless_of_case():
    official_api._require_response_host(
        cast(requests.Response, _StubResponse("https://WWW.TWSE.COM.TW/rwd/zh/fund/T86?date=20190102")),
        expected_host="www.twse.com.tw",
    )


def test_max_retry_sleep_bounds_the_delay_including_its_jitter():
    """The cap used to apply to the base only, so a 300s setting could sleep
    375s and the parameter did not mean what its name said."""
    delays = [official_api._retry_delay(None, attempt=attempt, retry_sleep=15.0, max_retry_sleep=300.0) for attempt in range(1, 12) for _ in range(50)]
    assert max(delays) <= 300.0
    assert max(delays) > 250.0, "the cap should still be reachable, not merely never approached"
