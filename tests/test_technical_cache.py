"""Synthetic reuse, preservation, integrity and interruption regressions."""

from __future__ import annotations

import gc
import json
import weakref
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from shp_utils import technical_cache as cache


def prices() -> pd.DataFrame:
    frames = []
    for code in ("1101", "1102"):
        values = np.arange(100.0, 130.0)
        frames.append(
            pd.DataFrame({
                "Code": code,
                "Date": pd.date_range("2020-01-01", periods=30),
                "Open": values,
                "High": values + 1,
                "Low": values - 1,
                "Close": values,
                "Volume": 100.0,
                "Source": "official",
            })
        )
    return pd.concat(frames, ignore_index=True)


def manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def object_ids(path: Path) -> dict[str, str]:
    return {entry["code"]: entry["object_id"] for entry in manifest(path)["objects"]}


def rewrite_manifest(root: Path, value: dict[str, Any]) -> Path:
    path = root / "manifests" / f"{cache._digest(value)}.json"
    path.write_bytes(cache._canonical(value))
    return path


def test_exact_reuse_skips_calculation_and_keeps_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = prices()
    first = cache.build_cache(frame, tmp_path, price_basis="raw-unadjusted")
    assert (first.computed_symbols, first.reused_symbols) == (2, 0)
    assert first.bytes_written > 0
    original = frame.copy(deep=True)

    def unexpected(_frame: pd.DataFrame) -> pd.DataFrame:
        raise AssertionError("unchanged symbols must not recompute")

    monkeypatch.setattr(cache.components, "compute_components", unexpected)
    second = cache.build_cache(frame.iloc[::-1], tmp_path, price_basis="raw-unadjusted")
    assert second.manifest_path == first.manifest_path
    assert (second.computed_symbols, second.reused_symbols, second.bytes_written) == (0, 2, 0)
    pd.testing.assert_frame_equal(frame, original)
    summary = cache.verify_manifest(first.manifest_path)
    assert summary["symbols"] == 2 and summary["rows"] == 60
    loaded = cache.load_features(first.manifest_path)
    assert len(loaded) == 60 and not loaded.duplicated(["Code", "Date"]).any()
    for entry in manifest(first.manifest_path)["objects"]:
        saved = pd.read_parquet(tmp_path / "objects" / entry["object_id"] / "input.parquet")
        normalized = cache.components.normalize_input(frame.loc[frame.Code.eq(entry["code"])]).reset_index(drop=True)
        pd.testing.assert_frame_equal(saved, normalized)


def test_historical_correction_recomputes_only_changed_symbol_and_preserves_old_manifest(tmp_path: Path) -> None:
    frame = prices()
    first = cache.build_cache(frame, tmp_path, price_basis="raw")
    old = cache.load_features(first.manifest_path)
    frame.loc[5, ["Open", "High", "Low", "Close"]] += 0.5
    second = cache.build_cache(frame, tmp_path, price_basis="raw")
    assert (second.computed_symbols, second.reused_symbols) == (1, 1)
    before, after = object_ids(first.manifest_path), object_ids(second.manifest_path)
    assert before["1101"] != after["1101"] and before["1102"] == after["1102"]
    pd.testing.assert_frame_equal(cache.load_features(first.manifest_path), old)


@pytest.mark.parametrize("via_build", [False, True])
def test_verification_releases_symbol_frames_without_concatenation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, via_build: bool) -> None:
    frame = prices()
    frame.loc[frame.Code.eq("1102"), "Date"] += pd.Timedelta(days=10)
    frame.loc[frame.Code.eq("1102"), "Source"] = "alternative"
    verify = cache.verify_manifest
    verify_object = cache._verify_object
    summaries = []

    def streaming_verify(path: Path) -> dict[str, Any]:
        references: list[weakref.ReferenceType[pd.DataFrame]] = []

        def checked_object(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], pd.DataFrame]:
            gc.collect()
            assert all(reference() is None for reference in references), "previous symbol features still retained"
            record, features = verify_object(*args, **kwargs)
            references.append(weakref.ref(features))
            return record, features

        def unexpected_concat(*args: Any, **kwargs: Any) -> None:
            pytest.fail("verification must not concatenate symbol frames")

        with monkeypatch.context() as patch:
            patch.setattr(cache, "_verify_object", checked_object)
            patch.setattr(cache.pd, "concat", unexpected_concat)
            summary = verify(path)
        gc.collect()
        assert len(references) == 2
        assert all(reference() is None for reference in references)
        summaries.append(summary)
        return summary

    if via_build:
        monkeypatch.setattr(cache, "verify_manifest", streaming_verify)
    result = cache.build_cache(frame, tmp_path, price_basis="raw")
    if not via_build:
        streaming_verify(result.manifest_path)
    assert len(summaries) == 1
    assert summaries[0]["symbols"] == 2 and summaries[0]["rows"] == 60
    assert summaries[0]["start_date"] == "2020-01-01T00:00:00"
    assert summaries[0]["end_date"] == "2020-02-09T00:00:00"
    assert summaries[0]["sources"] == ["alternative", "official"]
    assert len(cache.load_features(result.manifest_path)) == 60


def test_source_basis_runtime_and_implementation_change_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = prices()
    first = cache.build_cache(frame, tmp_path, price_basis="raw")
    frame.loc[0, "Source"] = "alternative"
    source_changed = cache.build_cache(frame, tmp_path, price_basis="raw")
    assert (source_changed.computed_symbols, source_changed.reused_symbols) == (1, 1)
    basis_changed = cache.build_cache(frame, tmp_path, price_basis="caller-adjusted")
    assert basis_changed.computed_symbols == 2
    runtime = {**cache._runtime(), "python": "future-version"}
    monkeypatch.setattr(cache, "_runtime", lambda: runtime)
    runtime_changed = cache.build_cache(frame, tmp_path, price_basis="caller-adjusted")
    assert runtime_changed.computed_symbols == 2
    implementation = {**cache._implementation(), "technical_cache.py": "a" * 64}
    monkeypatch.setattr(cache, "_implementation", lambda: implementation)
    implementation_changed = cache.build_cache(frame, tmp_path, price_basis="caller-adjusted")
    assert implementation_changed.computed_symbols == 2
    assert len(cache.load_features(first.manifest_path)) == 60


@pytest.mark.parametrize("name", ["input.parquet", "features.parquet", "object.json"])
def test_corrupt_artifacts_rejected_by_reader_and_builder_without_overwrite(tmp_path: Path, name: str) -> None:
    frame = prices()
    result = cache.build_cache(frame, tmp_path, price_basis="raw")
    entry = manifest(result.manifest_path)["objects"][0]
    artifact = tmp_path / "objects" / entry["object_id"] / name
    artifact.write_bytes(artifact.read_bytes() + b"corrupt")
    corrupted = artifact.read_bytes()
    with pytest.raises((ValueError, json.JSONDecodeError)):
        cache.load_features(result.manifest_path)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        cache.build_cache(frame, tmp_path, price_basis="raw")
    assert artifact.read_bytes() == corrupted
    assert not (tmp_path / "build.lock").exists()


@pytest.mark.parametrize("mutation", ["trailing-space", "pretty-json", "reordered-keys"])
def test_reformatted_object_metadata_cannot_be_laundered_into_new_manifest(tmp_path: Path, mutation: str) -> None:
    frame = prices()
    result = cache.build_cache(frame, tmp_path, price_basis="raw")
    entry = manifest(result.manifest_path)["objects"][0]
    artifact = tmp_path / "objects" / entry["object_id"] / "object.json"
    original = artifact.read_bytes()
    record = json.loads(original)
    if mutation == "trailing-space":
        changed = original + b" "
    elif mutation == "pretty-json":
        changed = json.dumps(record, indent=2).encode("utf-8")
    else:
        changed = json.dumps(dict(reversed(list(record.items()))), separators=(",", ":")).encode("utf-8")
    assert changed != original and json.loads(changed) == record
    artifact.write_bytes(changed)
    with pytest.raises(ValueError, match="object.json hash mismatch"):
        cache.verify_manifest(result.manifest_path)
    with pytest.raises(ValueError, match="canonical producer bytes"):
        cache.build_cache(frame, tmp_path, price_basis="raw")
    assert artifact.read_bytes() == changed
    assert list((tmp_path / "manifests").iterdir()) == [result.manifest_path]
    assert not (tmp_path / "build.lock").exists()


def test_foreign_lock_is_preserved_and_no_new_artifacts_are_written(tmp_path: Path) -> None:
    lock = tmp_path / "build.lock"
    lock.write_text("other-builder", encoding="utf-8")
    with pytest.raises(FileExistsError):
        cache.build_cache(prices(), tmp_path, price_basis="raw")
    assert lock.read_text(encoding="utf-8") == "other-builder"
    assert list(tmp_path.iterdir()) == [lock]


def test_interrupted_publication_retains_staging_and_publishes_no_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_staging: Path, _destination: Path) -> None:
        raise RuntimeError("simulated publication interruption")

    monkeypatch.setattr(cache, "_publish", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        cache.build_cache(prices(), tmp_path, price_basis="raw")
    assert not list((tmp_path / "objects").iterdir())
    assert not list((tmp_path / "manifests").iterdir())
    assert list((tmp_path / "staging").glob("*/object.json"))
    assert not (tmp_path / "build.lock").exists()


def test_manifest_publication_interruption_preserves_valid_objects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    publish = cache._publish

    def interrupt_manifest(staging: Path, destination: Path) -> None:
        if destination.parent.name == "manifests":
            raise RuntimeError("manifest interruption")
        publish(staging, destination)

    monkeypatch.setattr(cache, "_publish", interrupt_manifest)
    with pytest.raises(RuntimeError, match="manifest interruption"):
        cache.build_cache(prices(), tmp_path, price_basis="raw")
    assert len(list((tmp_path / "objects").iterdir())) == 2
    assert not list((tmp_path / "manifests").iterdir())
    assert list((tmp_path / "staging").glob("*.json"))
    monkeypatch.setattr(cache, "_publish", publish)
    result = cache.build_cache(prices(), tmp_path, price_basis="raw")
    assert (result.computed_symbols, result.reused_symbols) == (0, 2)


@pytest.mark.parametrize("bad_path", ["../object.json", "C:/escape.json", "C:escape.json", "objects\\escaped\\object.json", "/absolute.json"])
def test_manifest_paths_must_be_digest_derived(tmp_path: Path, bad_path: str) -> None:
    result = cache.build_cache(prices(), tmp_path, price_basis="raw")
    value = manifest(result.manifest_path)
    value["objects"][0]["path"] = bad_path
    path = rewrite_manifest(tmp_path, value)
    with pytest.raises(ValueError, match="digest-derived"):
        cache.verify_manifest(path)


def test_duplicate_objects_malformed_manifest_and_changed_body_rejected(tmp_path: Path) -> None:
    result = cache.build_cache(prices(), tmp_path, price_basis="raw")
    value = manifest(result.manifest_path)
    value["objects"].append(value["objects"][0])
    with pytest.raises(ValueError, match="duplicate"):
        cache.verify_manifest(rewrite_manifest(tmp_path, value))
    value = manifest(result.manifest_path)
    value.pop("coverage")
    with pytest.raises(ValueError, match="missing manifest"):
        cache.verify_manifest(rewrite_manifest(tmp_path, value))
    result.manifest_path.write_bytes(result.manifest_path.read_bytes() + b" ")
    # Whitespace is intentionally allowed: generation identity is canonical body.
    assert cache.verify_manifest(result.manifest_path)["rows"] == 60
    value = manifest(result.manifest_path)
    value["price_basis"] = "changed"
    result.manifest_path.write_bytes(cache._canonical(value))
    with pytest.raises(ValueError, match="digest/schema"):
        cache.verify_manifest(result.manifest_path)


def test_empty_input_basis_and_nan_infinity_identities(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty"):
        cache.build_cache(prices().iloc[:0], tmp_path, price_basis="raw")
    with pytest.raises(ValueError, match="price_basis"):
        cache.build_cache(prices(), tmp_path, price_basis=" ")
    frame = prices()
    identities = []
    for value in (np.nan, np.inf, -np.inf):
        frame.loc[0, "Close"] = value
        result = cache.build_cache(frame, tmp_path, price_basis="raw")
        identities.append(object_ids(result.manifest_path)["1101"])
    assert len(set(identities)) == 3


def test_linked_output_root_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation requires platform permission")
    with pytest.raises(ValueError, match="linked/reparse"):
        cache.build_cache(prices(), link, price_basis="raw")
    assert not list(target.iterdir())
