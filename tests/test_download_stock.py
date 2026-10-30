"""Pytest coverage for download_stock.py's forced re-fetch merge and usage-limit
logging, using a fake Shioaji API client (no real credentials or network calls).

Run with: uv run pytest test_download_stock.py -v
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import download_stock
import json5
import pandas as pd
import pytest
from shioaji.contracts import FetchStatus

from config import config

FAKE_CODE = "9527"


@dataclass
class FakeContract:
    code: str
    name: str
    category: str = "上市"


class FakeContractList:
    """Mimics shioaji's contract collection: iterable of contracts AND dict-like .get()."""

    def __init__(self, contracts: list[FakeContract]):
        self._by_code = {c.code: c for c in contracts}

    def __iter__(self):
        return iter(self._by_code.values())

    def get(self, code: str):
        return self._by_code.get(code)


class FakeIndexList(FakeContractList):
    """Mimics shioaji 1.2.9's index collection, whose membership test does not
    agree with its own lookup: keys() are exchange-prefixed symbols (TSE001),
    ``"001" in coll`` and ``"TSE001" in coll`` are both False, yet ``.get("001")``
    and ``coll["001"]`` return the contract. Code written as
    ``if "001" in ...Indexs.TSE`` therefore silently stops appending the market
    index -- data/001_day.csv stopped at 2025-12-15 for exactly this reason."""

    def keys(self):
        return [f"TSE{code}" for code in self._by_code]

    def __contains__(self, key: str) -> bool:
        return False

    def __getitem__(self, code: str):
        return self._by_code[code]


@dataclass
class FakeUsage:
    bytes: int = 0


@dataclass
class FakeApi:
    kbars_response: dict = field(default_factory=dict)
    usage_error: Exception | None = None
    # When set, kbars() raises the same error Shioaji returns for a
    # single request spanning more than this many days.
    max_range_days: int | None = None
    # When set, every kbars() call raises it -- a dead API session.
    kbars_error: Exception | None = None
    codes: list[str] = field(default_factory=lambda: [FAKE_CODE])
    kbars_calls: list[tuple[str, str]] = field(default_factory=list)
    kbars_codes: list[str] = field(default_factory=list)
    kbars_times: list[float] = field(default_factory=list)
    usage_calls: int = 0
    # Shioaji only flips this to Fetched once every security type has arrived;
    # a run that reads Contracts too early sees Fetching and a partial list.
    contracts_status: FetchStatus = FetchStatus.Fetched
    login_kwargs: dict = field(default_factory=dict)
    index_codes: list[str] = field(default_factory=list)

    def __post_init__(self):
        contracts = [FakeContract(code=code, name=f"Fake Co {code}") for code in self.codes]
        indexes = [FakeContract(code=code, name=f"Fake Index {code}") for code in self.index_codes]
        self.Contracts = SimpleNamespace(
            Stocks=SimpleNamespace(TSE=FakeContractList(contracts), OTC=FakeContractList([])),
            Indexs=SimpleNamespace(TSE=FakeIndexList(indexes)),
            status=self.contracts_status,
        )

    def login(self, api_key: str, secret_key: str, **kwargs) -> None:
        self.login_kwargs = kwargs

    def logout(self) -> None:
        pass

    def kbars(self, contract: FakeContract, start: str, end: str) -> dict:
        self.kbars_calls.append((start, end))
        self.kbars_codes.append(contract.code)
        self.kbars_times.append(time.monotonic())
        if self.kbars_error is not None:
            raise self.kbars_error
        if self.max_range_days is not None:
            span_days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days + 1
            if span_days > self.max_range_days:
                raise RuntimeError("{'status': {'status_code': 400}, 'response': {'detail': 'Kbars date range must not exceed 30 days.'}}")
            return {
                "ts": [f"{start} 09:00:00"],
                "Open": [1],
                "High": [1],
                "Low": [1],
                "Close": [1],
                "Volume": [1],
            }
        return self.kbars_response

    def usage(self) -> FakeUsage:
        self.usage_calls += 1
        if self.usage_error is not None:
            raise self.usage_error
        return FakeUsage(0)


@pytest.fixture
def isolated_download_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use scratch data and fake credentials, keeping the shipped category resource."""
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "STOCK_SYMBOL_MAPPING", str(tmp_path / "stock_symbol_mapping.json5"))
    # The real inter-request floor exists to keep a live session alive; against
    # a fake API it would only add minutes to the suite. The pacing test sets
    # its own value.
    monkeypatch.setattr(download_stock, "MIN_REQUEST_INTERVAL_SEC", 0.0)
    return tmp_path


def _install_fake_api(monkeypatch: pytest.MonkeyPatch, fake_api: FakeApi) -> None:
    monkeypatch.setattr(download_stock.sj, "Shioaji", lambda: fake_api)


def test_force_refetch_merges_without_duplicating_rows(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = isolated_download_env
    stock_file = data_dir / f"{FAKE_CODE}_min.csv"

    # Existing local data has a gap at 01-03 and a stale (wrong) value at 01-01.
    existing = pd.DataFrame({
        "ts": ["2024-01-01 09:00:00", "2024-01-02 09:00:00", "2024-01-04 09:00:00"],
        "Open": [10, 11, 13],
        "High": [10, 11, 13],
        "Low": [10, 11, 13],
        "Close": [10, 11, 13],
        "Volume": [100, 110, 130],
    })
    existing.to_csv(stock_file, index=False)

    # Forced re-fetch returns a corrected 01-01 row and a new 01-03 row filling the gap.
    kbars_response = {
        "ts": ["2024-01-01 09:00:00", "2024-01-03 09:00:00"],
        "Open": [99, 50],
        "High": [99, 50],
        "Low": [99, 50],
        "Close": [99, 50],
        "Volume": [999, 500],
    }
    _install_fake_api(monkeypatch, FakeApi(kbars_response=kbars_response))

    updated_count = download_stock.main(codes=[FAKE_CODE], start="2024-01-01", end="2024-01-05")

    assert updated_count == 1, "one symbol was written to, so the caller-visible count must be 1"
    result = pd.read_csv(stock_file)
    assert result["ts"].tolist() == sorted(result["ts"].tolist()), "rows must be sorted by ts"
    assert result["ts"].is_unique, "no duplicate ts rows after merge"
    assert len(result) == 4  # 01-01 (corrected), 01-02, 01-03 (new), 01-04

    by_ts = result.set_index("ts")
    assert by_ts.loc["2024-01-01 09:00:00", "Close"] == 99  # re-fetched value wins
    assert by_ts.loc["2024-01-02 09:00:00", "Close"] == 11  # untouched
    assert by_ts.loc["2024-01-03 09:00:00", "Close"] == 50  # gap filled
    assert by_ts.loc["2024-01-04 09:00:00", "Close"] == 13  # untouched


def test_usage_limit_check_failure_is_logged_not_swallowed(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    # No local file yet, so the incremental path requests the full default range;
    # an empty kbars response triggers the usage-limit check, which we make raise.
    _install_fake_api(
        monkeypatch,
        FakeApi(kbars_response={}, usage_error=RuntimeError("usage endpoint unavailable")),
    )

    with caplog.at_level(logging.WARNING, logger=download_stock.__name__):
        updated_count = download_stock.main(codes=[FAKE_CODE])

    assert any("usage endpoint unavailable" in record.message for record in caplog.records), (
        "the usage-limit check's exception must be logged, not silently swallowed"
    )
    assert updated_count == 0, "an empty kbars response means nothing new was written for any symbol"


def test_aborts_once_the_api_stops_answering(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """A dead session answers nothing -- kbars times out and so does the usage()
    quota check, so the quota guard never fires. Without a second brake the run
    walks the whole contract list at ~35s of timeout per symbol (three wasted
    hours on 2026-08-14). It must give up after MAX_CONSECUTIVE_FAILURES symbols
    and fail loudly, so run_daily.py reports step 1 as failed."""
    limit = download_stock.MAX_CONSECUTIVE_FAILURES
    codes = [str(9000 + i) for i in range(limit + 5)]
    fake_api = FakeApi(
        codes=codes,
        kbars_error=RuntimeError("read timeout"),
        usage_error=RuntimeError("usage endpoint timed out too"),
    )
    _install_fake_api(monkeypatch, fake_api)

    with pytest.raises(download_stock.DownloadAborted):
        download_stock.main(codes=codes)

    assert len(dict.fromkeys(fake_api.kbars_codes)) == limit, f"must stop after {limit} consecutive failures instead of walking all {len(codes)} symbols"


def test_symbols_without_new_bars_do_not_trip_the_abort(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """An empty kbars response is not an API failure -- a symbol can simply have
    no new bars (suspended, no trades). Only raised errors count towards the
    abort, otherwise a quiet market would kill an otherwise healthy run."""
    codes = [str(9000 + i) for i in range(download_stock.MAX_CONSECUTIVE_FAILURES + 5)]
    fake_api = FakeApi(codes=codes, kbars_response={})
    _install_fake_api(monkeypatch, fake_api)

    updated_count = download_stock.main(codes=codes)

    assert updated_count == 0
    assert len(dict.fromkeys(fake_api.kbars_codes)) == len(codes), "empty (but successful) responses must not abort the run"


def test_requests_are_paced_so_the_server_keeps_the_session(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """The server drops the session with a bogus 401 "Token is expired" when
    requests arrive too fast -- measured on 2026-08-16: ~64 req/s died after 340
    symbols, ~20 req/s survived 1200. Empty responses come back instantly, so
    without a floor between requests a non-trading day free-runs straight into
    that limit."""
    interval = 0.02
    monkeypatch.setattr(download_stock, "MIN_REQUEST_INTERVAL_SEC", interval)
    codes = [str(9000 + i) for i in range(10)]
    fake_api = FakeApi(codes=codes, kbars_response={})
    _install_fake_api(monkeypatch, fake_api)

    # A short forced range keeps it at one request per symbol, so the gaps
    # measured below are between symbols rather than within one symbol's chunks.
    download_stock.main(codes=codes, start="2024-01-01", end="2024-01-05")

    assert len(fake_api.kbars_times) == len(codes)
    gaps = [b - a for a, b in zip(fake_api.kbars_times, fake_api.kbars_times[1:])]
    # Timer granularity makes an exact floor flaky; 80% of it still proves the
    # calls were spaced rather than fired back to back.
    assert min(gaps) >= interval * 0.8, f"requests were not paced: smallest gap {min(gaps):.4f}s"


def test_the_quota_check_does_not_run_once_per_symbol(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """The 500MB quota is a whole-day number that barely moves across a thousand
    symbols, so polling it per symbol bought nothing and doubled the request
    count -- which is what pushed the run over the rate the server tolerates."""
    monkeypatch.setattr(download_stock, "USAGE_CHECK_INTERVAL_SEC", 60.0)
    codes = [str(9000 + i) for i in range(25)]
    fake_api = FakeApi(codes=codes, kbars_response={})
    _install_fake_api(monkeypatch, fake_api)

    download_stock.main(codes=codes, start="2024-01-01", end="2024-01-05")

    assert len(fake_api.kbars_codes) == len(codes), "every symbol should still be attempted"
    assert fake_api.usage_calls == 1, f"the quota check ran {fake_api.usage_calls} times for {len(codes)} symbols; it must be throttled, not called per symbol"


def test_tracked_etf_survives_the_warrant_category_filter():
    """Shioaji gives ETFs the same category as warrants ("00"), so the filter
    that keeps ~31,000 warrants out also dropped every ETF -- including 0050,
    which check_data_integrity.py relies on as a reference symbol and which
    therefore warned about a missing day file on every single run."""
    warrant = FakeContract(code="078457", name="Some Warrant", category="00")
    etf = FakeContract(code="0050", name="元大台灣50", category="00")
    stock = FakeContract(code="2330", name="TSMC", category="24")
    api = SimpleNamespace(
        Contracts=SimpleNamespace(
            Stocks=SimpleNamespace(
                TSE=FakeContractList([warrant, etf, stock]),
                OTC=FakeContractList([]),
            ),
            Indexs=SimpleNamespace(TSE={}),
        )
    )
    stock_category = json5.loads(config.STOCK_CATEGORY.read_text(encoding="utf-8"))
    assert isinstance(stock_category, dict)

    codes = [c.code for c in download_stock.get_contract_list(cast(download_stock.sj.Shioaji, api), stock_category)]

    assert "0050" in codes, "the tracked ETF must be downloaded despite its warrant category"
    assert "078457" not in codes, "warrants must still be excluded"
    assert codes.count("0050") == 1, "the tracked code must not be added twice"
    assert "2330" in codes


def test_chunk_date_ranges_splits_long_span_into_30_day_windows():
    chunks = download_stock._chunk_date_ranges("2024-01-01", "2024-02-14")  # 45-day span
    assert chunks == [
        ("2024-01-01", "2024-01-30"),
        ("2024-01-31", "2024-02-14"),
    ]


def test_chunk_date_ranges_keeps_span_within_limit_as_single_chunk():
    assert download_stock._chunk_date_ranges("2024-01-01", "2024-01-05") == [("2024-01-01", "2024-01-05")]


def test_chunk_date_ranges_returns_empty_list_when_start_after_end():
    assert download_stock._chunk_date_ranges("2024-01-05", "2024-01-01") == []


def test_download_splits_requests_exceeding_30_days_into_chunks(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """Shioaji's kbars endpoint rejects a single request spanning more than 30
    days; download_stock must split a wide date range into <=30-day chunks
    instead of skipping the whole download."""
    fake_api = FakeApi(max_range_days=30)
    _install_fake_api(monkeypatch, fake_api)

    stock_file = isolated_download_env / f"{FAKE_CODE}_min.csv"

    updated_count = download_stock.main(codes=[FAKE_CODE], start="2024-01-01", end="2024-02-14")  # 45-day span

    assert updated_count == 1
    assert len(fake_api.kbars_calls) > 1, "a 45-day range must be split into multiple <=30-day requests"
    for call_start, call_end in fake_api.kbars_calls:
        span_days = (datetime.strptime(call_end, "%Y-%m-%d") - datetime.strptime(call_start, "%Y-%m-%d")).days + 1
        assert span_days <= 30, f"chunk {call_start}..{call_end} exceeds the 30-day API limit"

    assert stock_file.is_file()
    result = pd.read_csv(stock_file)
    assert len(result) == len(fake_api.kbars_calls), "one row per chunk should be saved"


def test_returns_zero_when_already_up_to_date(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """A symbol whose local file already covers up to today needs no request at
    all (the incremental start date is >= today), so the run-level "anything
    new?" count run_daily.py relies on must stay 0 -- not just "no rows
    written", but genuinely "no API calls made"."""
    stock_file = isolated_download_env / f"{FAKE_CODE}_min.csv"
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    pd.DataFrame({
        "ts": [f"{tomorrow} 09:00:00"],
        "Open": [10],
        "High": [10],
        "Low": [10],
        "Close": [10],
        "Volume": [100],
    }).to_csv(stock_file, index=False)

    fake_api = FakeApi()
    _install_fake_api(monkeypatch, fake_api)

    updated_count = download_stock.main(codes=[FAKE_CODE])

    assert updated_count == 0
    assert fake_api.kbars_calls == [], "already up to date -- no API call should have been made at all"


def _write_mapping(path: Path, count: int) -> None:
    """Write a plausible existing mapping so the shrink guard has a baseline."""
    mapping = {str(1000 + i): f"Fake Co {1000 + i}" for i in range(count)}
    path.write_text(json.dumps(mapping, ensure_ascii=False, indent=4), encoding="utf-8")


def test_login_waits_for_the_contract_list(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """Shioaji's login defaults to contracts_timeout=0, meaning it returns before
    the contract list has downloaded. Reading api.Contracts at that point yields a
    partial snapshot -- on 2026-08-27 and 08-28 that was 332 warrants and no
    ordinary stock. The run must ask login to wait."""
    fake_api = FakeApi(kbars_response={})
    _install_fake_api(monkeypatch, fake_api)

    download_stock.main(codes=[FAKE_CODE])

    timeout = fake_api.login_kwargs.get("contracts_timeout")
    assert timeout, "login must pass a non-zero contracts_timeout so it waits for the contract list"


def test_a_half_downloaded_contract_list_aborts_the_run(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """Even with a timeout, a slow fetch can expire and leave status at Fetching.
    Continuing from there would download a fraction of the market and report success."""
    mapping_file = Path(config.STOCK_SYMBOL_MAPPING)
    _write_mapping(mapping_file, 500)
    before = mapping_file.read_text(encoding="utf-8")

    _install_fake_api(monkeypatch, FakeApi(kbars_response={}, contracts_status=FetchStatus.Fetching))

    with pytest.raises(download_stock.DownloadAborted):
        download_stock.main(codes=[FAKE_CODE])

    assert mapping_file.read_text(encoding="utf-8") == before, "an unfinished contract list must not touch the mapping file"


def test_a_collapsed_contract_list_does_not_overwrite_the_mapping(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """The durable guard, independent of Shioaji's own status flag: a mapping that
    loses most of its entries means the contract list was read too early, not that
    the market shrank. The backup mirror is additive, so it copies an overwrite
    faithfully -- this refusal is the last thing standing between a partial read and
    a destroyed 45k-entry artifact plus a destroyed backup copy of it."""
    mapping_file = Path(config.STOCK_SYMBOL_MAPPING)
    _write_mapping(mapping_file, 500)
    before = mapping_file.read_text(encoding="utf-8")

    # Contracts claim to be fully fetched, but only a handful arrived.
    _install_fake_api(monkeypatch, FakeApi(kbars_response={}, codes=["9527", "9528"]))

    with pytest.raises(download_stock.DownloadAborted, match="對照表"):
        download_stock.main(codes=[FAKE_CODE])

    assert mapping_file.read_text(encoding="utf-8") == before, "the shrunken mapping must not be written"


def test_a_healthy_contract_list_still_updates_the_mapping(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """The guards must not block the normal case: a mapping that grows, or that has
    no previous version at all, is written as before."""
    mapping_file = Path(config.STOCK_SYMBOL_MAPPING)
    _write_mapping(mapping_file, 2)

    codes = [str(9000 + i) for i in range(20)]
    _install_fake_api(monkeypatch, FakeApi(kbars_response={}, codes=codes))

    download_stock.main(codes=[codes[0]])

    written = json.loads(mapping_file.read_text(encoding="utf-8"))
    assert set(written) == set(codes), "a healthy contract list must still refresh the mapping"


def test_the_market_index_is_downloaded_even_though_membership_lies(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """shioaji 1.2.9's index collection answers False to every membership test while
    still resolving the same code through .get(), so an ``if "001" in ...`` gate is
    permanently closed. The index must be selected by lookup, not by membership."""
    fake_api = FakeApi(kbars_response={}, codes=["2330"], index_codes=["001"])
    _install_fake_api(monkeypatch, fake_api)

    contract_list = download_stock.get_contract_list(cast(download_stock.sj.Shioaji, fake_api), {})

    assert "001" in {contract.code for contract in contract_list}, "the market index must be in the download list"

    mapping_file = Path(config.STOCK_SYMBOL_MAPPING)
    download_stock.update_stock_symbol_mapping(cast(download_stock.sj.Shioaji, fake_api))
    assert "001" in json.loads(mapping_file.read_text(encoding="utf-8")), "the market index must be in the symbol mapping"


def test_the_market_index_can_be_requested_by_code(isolated_download_env: Path, monkeypatch: pytest.MonkeyPatch):
    """--codes 001 has to reach the index collection too, otherwise the market
    index cannot be backfilled on its own after a gap."""
    fake_api = FakeApi(kbars_response={}, codes=["2330"], index_codes=["001"])

    assert download_stock.get_contract_by_code(cast(download_stock.sj.Shioaji, fake_api), "001") is not None, "the market index must be resolvable by code"
    assert download_stock.get_contract_by_code(cast(download_stock.sj.Shioaji, fake_api), "2330") is not None, "ordinary stocks must still resolve"
    assert download_stock.get_contract_by_code(cast(download_stock.sj.Shioaji, fake_api), "9999") is None, "an unknown code must still resolve to None"


def test_read_existing_mapping_count_reads_json_and_json5(tmp_path: Path):
    strict = tmp_path / "strict.json5"
    strict.write_text('{"2330": "台積電", "2317": "鴻海"}', encoding="utf-8")
    relaxed = tmp_path / "relaxed.json5"
    relaxed.write_text('{"2330": "台積電", // comment\n "2317": "鴻海",}', encoding="utf-8")

    assert download_stock.read_existing_mapping_count(strict) == 2
    assert download_stock.read_existing_mapping_count(relaxed) == 2
    assert download_stock.read_existing_mapping_count(tmp_path / "missing.json5") == 0


@pytest.mark.parametrize(
    ("kbars_error", "kbars_response", "expected"),
    [
        (RuntimeError("read timeout"), {}, {"timeout": 1, "no_data": 0, "error": 0}),
        (RuntimeError("boom"), {}, {"timeout": 0, "no_data": 0, "error": 1}),
        (None, {}, {"timeout": 0, "no_data": 1, "error": 0}),
    ],
    ids=["timeout", "error", "no_data"],
)
def test_download_summary_breaks_skipped_symbols_down_by_reason(
    isolated_download_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    kbars_error: Exception | None,
    kbars_response: dict,
    expected: dict[str, int],
):
    fake_api = FakeApi(codes=["9001"], kbars_error=kbars_error, kbars_response=kbars_response)
    _install_fake_api(monkeypatch, fake_api)

    with caplog.at_level(logging.INFO, logger=download_stock.__name__):
        assert download_stock.main(codes=["9001"]) == 0

    summary = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("download_summary:"))
    assert "skipped_failed=1" in summary
    assert f"skip_reasons={expected}" in summary
