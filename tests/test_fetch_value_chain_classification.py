from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fetch_value_chain_classification as value_chain
import pytest
import requests

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _sample_snapshot(*, fetched_at: datetime) -> dict:
    pcb = value_chain.parse_chain_page(
        _fixture("value_chain_pcb.html"),
        chain_code="L000",
        source_url="https://ic.tpex.org.tw/introduce.php?ic=L000",
    )
    memory = value_chain.parse_chain_page(
        _fixture("value_chain_memory.html"),
        chain_code="D000",
        source_url="https://ic.tpex.org.tw/introduce.php?ic=D000",
    )
    return value_chain.build_snapshot(
        [memory, pcb],
        fetched_at=fetched_at,
        index_url="https://ic.tpex.org.tw/introduce.php?ic=D000",
    )


def test_discovers_chain_codes_in_published_order() -> None:
    assert value_chain.discover_chain_codes(_fixture("value_chain_pcb.html")) == [
        ("D000", "測試產業甲"),
        ("L000", "測試產業乙"),
        ("J000", "測試產業丙"),
    ]


def test_parses_stages_domestic_members_and_excludes_external_companies() -> None:
    chain = value_chain.parse_chain_page(
        _fixture("value_chain_pcb.html"),
        chain_code="L000",
        source_url="https://ic.tpex.org.tw/introduce.php?ic=L000",
    )

    assert chain["name"] == "測試產業乙"
    glass_fiber = chain["nodes"][0]
    assert glass_fiber["code"] == "L100"
    assert glass_fiber["stage"] == "上游"
    assert [member["code"] for member in glass_fiber["members"]] == ["9001", "9002", "9003"]
    assert glass_fiber["members"][2]["listing_segment"] == "本國上櫃公司"
    assert all(member["name"] != "Foreign Glass" for member in glass_fiber["members"])
    assert all(member["code"] != "9999" for member in glass_fiber["members"])


def test_preserves_null_stage_when_source_has_no_stage_label() -> None:
    html = """
    <h3>體驗科技產業鏈簡介</h3>
    <div class="chain chain1">
      <h4>關鍵硬體零組件</h4>
      <div id="ic_link_5710" class="company-chain-panel">處理器/IC</div>
    </div>
    <div id="companyList_5710" title="處理器/IC" class="x-hidden">
      <b>本國上市公司(1家)</b>
      <a href="company_basic.php?stk_code=9007" title="測試公司庚">測試公司庚</a>
    </div>
    """

    chain = value_chain.parse_chain_page(
        html,
        chain_code="5700",
        source_url="https://ic.tpex.org.tw/introduce.php?ic=5700",
    )

    assert chain["nodes"][0]["stage"] is None


def test_builds_deterministic_inverse_symbol_memberships() -> None:
    snapshot = _sample_snapshot(fetched_at=datetime(2026, 8, 23, 11, 0, tzinfo=UTC))

    assert snapshot["schema_version"] == 1
    memory_membership = snapshot["symbols"]["9005"]["memberships"][0]
    assert memory_membership["node_name"] == "測試子分類"
    assert memory_membership["node_code"] == "D150"
    assert snapshot["symbols"]["9001"]["listing_segments"] == ["本國上市公司"]
    assert snapshot["source"]["chain_count"] == 2
    assert len(snapshot["snapshot_id"]) == 64


def test_refresh_writes_identical_latest_and_append_only_history(tmp_path: Path) -> None:
    observed = datetime(2026, 8, 23, 11, 0, tzinfo=UTC)
    snapshot = _sample_snapshot(fetched_at=observed)

    result = value_chain.refresh_value_chain_classification(
        data_dir=tmp_path,
        force=True,
        now=observed,
        fetcher=lambda **_kwargs: snapshot,
    )

    assert result.refreshed is True
    assert result.history_path is not None
    assert result.history_path.name == "2026-08-23T110000Z.json"
    assert result.latest_path.read_bytes() == result.history_path.read_bytes()
    text = result.latest_path.read_text(encoding="utf-8")
    assert "測試子分類" in text
    assert "\\u6e2c" not in text


def test_fresh_latest_skips_network_and_leaves_files_unchanged(tmp_path: Path) -> None:
    observed = datetime(2026, 8, 23, 11, 0, tzinfo=UTC)
    snapshot = _sample_snapshot(fetched_at=observed)
    first = value_chain.refresh_value_chain_classification(
        data_dir=tmp_path,
        force=True,
        now=observed,
        fetcher=lambda **_kwargs: snapshot,
    )
    before = first.latest_path.read_bytes()

    def must_not_fetch(**_kwargs):
        raise AssertionError("fresh classification must not touch the network")

    result = value_chain.refresh_value_chain_classification(
        data_dir=tmp_path,
        now=observed + timedelta(days=6),
        fetcher=must_not_fetch,
    )

    assert result.refreshed is False
    assert result.latest_path.read_bytes() == before
    assert len(list((tmp_path / value_chain.DEFAULT_HISTORY_DIR.name).glob("*.json"))) == 1


def test_corrupt_latest_triggers_refresh(tmp_path: Path) -> None:
    (tmp_path / value_chain.DEFAULT_LATEST_PATH.name).write_text("not json", encoding="utf-8")
    observed = datetime(2026, 8, 23, 11, 0, tzinfo=UTC)
    calls = 0

    def fetcher(**_kwargs):
        nonlocal calls
        calls += 1
        return _sample_snapshot(fetched_at=observed)

    value_chain.refresh_value_chain_classification(data_dir=tmp_path, now=observed, fetcher=fetcher)

    assert calls == 1
    assert json.loads((tmp_path / value_chain.DEFAULT_LATEST_PATH.name).read_text(encoding="utf-8"))["schema_version"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda snapshot: snapshot.__setitem__("chains", [None]),
        lambda snapshot: snapshot["chains"][0].__setitem__("nodes", [None]),
        lambda snapshot: snapshot.__setitem__("snapshot_id", "0" * 64),
        lambda snapshot: snapshot.__setitem__("symbols", {}),
        lambda snapshot: snapshot["source"].__setitem__("chain_count", 999),
    ],
    ids=["chain-type", "node-type", "snapshot-id", "symbol-index", "chain-count"],
)
def test_structurally_corrupt_latest_triggers_refresh(tmp_path: Path, mutate: Callable[[dict], None]) -> None:
    observed = datetime(2026, 8, 23, 11, 0, tzinfo=UTC)
    corrupt = _sample_snapshot(fetched_at=observed)
    mutate(corrupt)
    (tmp_path / value_chain.DEFAULT_LATEST_PATH.name).write_text(
        json.dumps(corrupt, ensure_ascii=False),
        encoding="utf-8",
    )
    calls = 0

    def fetcher(**_kwargs):
        nonlocal calls
        calls += 1
        return _sample_snapshot(fetched_at=observed)

    value_chain.refresh_value_chain_classification(data_dir=tmp_path, now=observed, fetcher=fetcher)

    assert calls == 1


def test_invalid_refresh_keeps_last_good_latest(tmp_path: Path) -> None:
    observed = datetime(2026, 8, 23, 11, 0, tzinfo=UTC)
    snapshot = _sample_snapshot(fetched_at=observed)
    first = value_chain.refresh_value_chain_classification(
        data_dir=tmp_path,
        force=True,
        now=observed,
        fetcher=lambda **_kwargs: snapshot,
    )
    before = first.latest_path.read_bytes()

    with pytest.raises(ValueError, match="missing fields"):
        value_chain.refresh_value_chain_classification(
            data_dir=tmp_path,
            force=True,
            now=observed + timedelta(days=1),
            fetcher=lambda **_kwargs: {"schema_version": 1},
        )

    assert first.latest_path.read_bytes() == before
    assert len(list((tmp_path / value_chain.DEFAULT_HISTORY_DIR.name).glob("*.json"))) == 1


def test_history_collision_never_deletes_existing_snapshot(tmp_path: Path) -> None:
    observed = datetime(2026, 8, 23, 11, 0, tzinfo=UTC)
    snapshot = _sample_snapshot(fetched_at=observed)
    first = value_chain.refresh_value_chain_classification(
        data_dir=tmp_path,
        force=True,
        now=observed,
        fetcher=lambda **_kwargs: snapshot,
    )
    assert first.history_path is not None
    before = first.history_path.read_bytes()

    with pytest.raises(FileExistsError):
        value_chain.refresh_value_chain_classification(
            data_dir=tmp_path,
            force=True,
            now=observed,
            fetcher=lambda **_kwargs: snapshot,
        )

    assert first.history_path.read_bytes() == before


def test_fetch_retries_transport_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    class Response:
        status_code = 200
        url = "https://ic.tpex.org.tw/introduce.php?ic=L000"
        headers: dict[str, str] = {}

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def iter_content(*, chunk_size: int):
            assert chunk_size > 0
            yield b"<html>ok</html>"

        @staticmethod
        def close() -> None:
            return None

    def fake_get(*_args, **kwargs):
        nonlocal attempts
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        attempts += 1
        if attempts == 1:
            raise requests.ConnectionError("temporary")
        return Response()

    monkeypatch.setattr(value_chain.requests, "get", fake_get)
    monkeypatch.setattr(value_chain.time, "sleep", lambda _seconds: None)

    html, url = value_chain.fetch_html_with_retry(
        value_chain.CHAIN_URL,
        params={"ic": "L000"},
        retries=1,
        retry_sleep=0,
    )

    assert attempts == 2
    assert html == "<html>ok</html>"
    assert url.endswith("ic=L000")


def test_fetch_rejects_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    class RedirectResponse:
        status_code = 302
        url = "https://ic.tpex.org.tw/introduce.php?ic=L000"
        headers = {"Location": "https://evil.example/"}

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(value_chain.requests, "get", lambda *_args, **_kwargs: RedirectResponse())

    with pytest.raises(RuntimeError, match="redirected"):
        value_chain.fetch_html_with_retry(value_chain.CHAIN_URL, params={"ic": "L000"}, retries=0)


def test_fetch_rejects_external_final_url(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExternalResponse:
        status_code = 200
        url = "https://evil.example/company_basic.php"
        headers: dict[str, str] = {}

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(value_chain.requests, "get", lambda *_args, **_kwargs: ExternalResponse())

    with pytest.raises(ValueError, match="unexpected TPEx"):
        value_chain.fetch_html_with_retry(value_chain.CHAIN_URL, params={"ic": "L000"}, retries=0)


def test_fetch_rejects_oversized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class OversizedResponse:
        status_code = 200
        url = "https://ic.tpex.org.tw/introduce.php?ic=L000"
        headers = {"Content-Length": str(value_chain.MAX_RESPONSE_BYTES + 1)}

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(value_chain.requests, "get", lambda *_args, **_kwargs: OversizedResponse())

    with pytest.raises(ValueError, match="exceeded"):
        value_chain.fetch_html_with_retry(value_chain.CHAIN_URL, params={"ic": "L000"}, retries=0)
