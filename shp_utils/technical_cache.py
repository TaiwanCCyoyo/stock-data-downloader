"""Additive, content-addressed technical components with retained replay input."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd
import pyarrow

from shp_utils import technical_components as components

LOGGER = logging.getLogger(__name__)
MANIFEST_VERSION = "technical-cache-manifest.v1"
OBJECT_VERSION = "technical-cache-object.v1"
KEYS = ["Code", "Date"]
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class BuildResult:
    manifest_path: Path
    computed_symbols: int
    reused_symbols: int
    bytes_written: int


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _safe(path: Path) -> Path:
    """Disallow linked/reparse ancestors before reading or publishing evidence."""
    if ".." in path.parts:
        raise ValueError("path traversal forbidden")
    absolute = path.absolute()
    for ancestor in (absolute, *absolute.parents):
        if ancestor.exists() or ancestor.is_symlink():
            attributes = getattr(ancestor.lstat(), "st_file_attributes", 0)
            if ancestor.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                raise ValueError(f"linked/reparse path forbidden: {ancestor}")
    return absolute


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(_safe(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    with path.open("xb") as stream:
        stream.write(_canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def _date_text(value: Any) -> str:
    timestamp = pd.Timestamp(value)
    if not isinstance(timestamp, pd.Timestamp):
        raise ValueError("missing date cannot identify an observation")
    return timestamp.isoformat()


def _scalar(value: Any) -> Any:
    if isinstance(value, (float, np.floating)):
        # Hex floats preserve binary precision and distinguish NaN and infinities.
        return {"float": float(value).hex()}
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return {"timestamp": _date_text(value)}
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, str):
        return value
    raise ValueError(f"unsupported normalized input value: {type(value).__name__}")


def _schema(frame: pd.DataFrame) -> list[dict[str, str]]:
    return [{"name": str(name), "dtype": str(dtype)} for name, dtype in frame.dtypes.items()]


def _input_digest(frame: pd.DataFrame) -> str:
    return _digest({"schema": _schema(frame), "rows": [[_scalar(value) for value in row] for row in frame.itertuples(index=False, name=None)]})


def _runtime() -> dict[str, str]:
    return {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__, "pyarrow": pyarrow.__version__}


def _implementation() -> dict[str, str]:
    return {"technical_cache.py": _file_digest(Path(__file__)), "technical_components.py": _file_digest(Path(components.__file__))}


def _coverage(frame: pd.DataFrame) -> dict[str, Any]:
    dates = cast(pd.Series, frame["Date"])
    sources = cast(pd.Series, frame["Source"])
    return {
        "rows": len(frame),
        "start_date": _date_text(dates.min()),
        "end_date": _date_text(dates.max()),
        "sources": sorted(str(value) for value in sources.dropna().unique()),
    }


def _keys(frame: pd.DataFrame, code: str | None = None) -> None:
    if any(key not in frame for key in KEYS) or frame.empty:
        raise ValueError("nonempty Code/Date keys required")
    keys = cast(pd.DataFrame, frame[KEYS])
    if keys.isna().to_numpy().any() or frame.duplicated(KEYS).any():
        raise ValueError("missing or duplicate Code/Date keys")
    dates = cast(pd.Series, frame["Date"])
    if not pd.api.types.is_datetime64_any_dtype(dates.dtype) or dates.dt.tz is not None or not dates.eq(dates.dt.normalize()).all():
        raise ValueError("Date must be timezone-naive midnight timestamps")
    codes = cast(pd.Series, frame["Code"])
    if not codes.map(lambda value: isinstance(value, str) and bool(value)).all() or (code is not None and not codes.eq(code).all()):
        raise ValueError("Code identity mismatch")


def _verify_object(
    root: Path,
    object_id: str,
    expected_json_hash: str | None = None,
    *,
    staging: Path | None = None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    if not isinstance(object_id, str) or not _DIGEST.fullmatch(object_id):
        raise ValueError("invalid object digest")
    directory = _safe(root / "objects" / object_id if staging is None else staging)
    object_path = _safe(directory / "object.json")
    if expected_json_hash is not None and _file_digest(object_path) != expected_json_hash:
        raise ValueError("object.json hash mismatch")
    record = _json(object_path)
    if object_path.read_bytes() != _canonical(record):
        raise ValueError("object.json must retain canonical producer bytes")
    if record.get("schema_version") != OBJECT_VERSION or record.get("object_id") != object_id:
        raise ValueError("object identity mismatch")
    identity = record.get("identity")
    if not isinstance(identity, dict) or _digest(identity) != object_id:
        raise ValueError("object identity payload mismatch")
    if not isinstance(record.get("code"), str) or not record["code"]:
        raise ValueError("object code required")
    if isinstance(record.get("rows"), bool) or not isinstance(record.get("rows"), int) or record["rows"] <= 0:
        raise ValueError("positive object row count required")
    for key in ("contract_version", "price_basis", "runtime", "implementation", "input_schema", "normalized_input_digest"):
        if key not in identity:
            raise ValueError(f"missing identity field: {key}")
    frames = []
    for name in ("input", "features"):
        path = _safe(directory / f"{name}.parquet")
        sha = record.get(f"{name}_sha256")
        if not isinstance(sha, str) or not _DIGEST.fullmatch(sha) or _file_digest(path) != sha:
            raise ValueError(f"{name} hash mismatch")
        frame = pd.read_parquet(path)
        _keys(frame, record.get("code"))
        if len(frame) != record.get("rows") or _schema(frame) != record.get(f"{name}_schema"):
            raise ValueError(f"{name} rows/schema mismatch")
        frames.append(frame)
    input_frame, features = frames
    if _schema(input_frame) != identity["input_schema"] or _input_digest(input_frame) != identity["normalized_input_digest"]:
        raise ValueError("normalized input digest mismatch")
    if record.get("normalized_input_digest") != identity["normalized_input_digest"] or _coverage(input_frame) != record.get("coverage"):
        raise ValueError("object input evidence mismatch")
    if not cast(pd.DataFrame, input_frame[KEYS]).equals(cast(pd.DataFrame, features[KEYS])):
        raise ValueError("input/features key alignment mismatch")
    price_valid = cast(pd.Series, features["price_valid"]) if "price_valid" in features else None
    if price_valid is None or not pd.api.types.is_bool_dtype(price_valid.dtype) or price_valid.isna().any():
        raise ValueError("features require boolean price_valid")
    return record, features


def _read_manifest(manifest_path: Path) -> tuple[Path, dict[str, Any]]:
    path = _safe(manifest_path)
    if path.parent.name != "manifests" or not _DIGEST.fullmatch(path.stem) or path.suffix != ".json":
        raise ValueError("manifest path must be manifests/<digest>.json")
    manifest = _json(path)
    if _digest(manifest) != path.stem or manifest.get("schema_version") != MANIFEST_VERSION:
        raise ValueError("manifest digest/schema mismatch")
    for key in ("contract_version", "price_basis", "runtime", "implementation", "definitions", "columns", "coverage"):
        if key not in manifest:
            raise ValueError(f"missing manifest field: {key}")
    for key in ("contract_version", "price_basis"):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise ValueError(f"manifest {key} must be nonempty text")
    for key in ("runtime", "implementation", "definitions", "columns", "coverage"):
        if not isinstance(manifest[key], dict) or not manifest[key]:
            raise ValueError(f"manifest {key} must be a nonempty object")
    for key in ("input", "features"):
        columns = manifest["columns"].get(key)
        if not isinstance(columns, list) or not all(isinstance(name, str) for name in columns) or len(columns) != len(set(columns)):
            raise ValueError("manifest columns must be unique string lists")
    if not isinstance(manifest.get("objects"), list) or not manifest["objects"]:
        raise ValueError("nonempty manifest objects required")
    return path.parent.parent, manifest


def _verified(manifest_path: Path, *, collect_frames: bool = False) -> tuple[dict[str, Any], list[pd.DataFrame]]:
    root, manifest = _read_manifest(manifest_path)
    frames: list[pd.DataFrame] = []
    codes: set[str] = set()
    object_ids: set[str] = set()
    total = 0
    start_date: str | None = None
    end_date: str | None = None
    sources: set[str] = set()
    for entry in manifest["objects"]:
        if not isinstance(entry, dict):
            raise ValueError("manifest object must be a record")
        object_id = entry.get("object_id")
        if not isinstance(object_id, str) or not _DIGEST.fullmatch(object_id) or object_id in object_ids:
            raise ValueError("invalid or duplicate manifest object digest")
        object_ids.add(object_id)
        if entry.get("path") != f"objects/{object_id}/object.json":
            raise ValueError("object path must be digest-derived")
        sha = entry.get("sha256")
        if not isinstance(sha, str) or not _DIGEST.fullmatch(sha):
            raise ValueError("object.json SHA256 required")
        record, frame = _verify_object(root, object_id, sha)
        code = record.get("code")
        if not isinstance(code, str) or code in codes or code != entry.get("code"):
            raise ValueError("invalid or duplicate object code")
        codes.add(code)
        if record.get("rows") != entry.get("rows") or record.get("coverage") != entry.get("coverage"):
            raise ValueError("object coverage mismatch")
        identity = record["identity"]
        for key in ("contract_version", "price_basis", "runtime", "implementation"):
            if identity[key] != manifest[key]:
                raise ValueError(f"manifest/object {key} mismatch")
        if list(frame.columns) != manifest["columns"]["features"] or [item["name"] for item in record["input_schema"]] != manifest["columns"]["input"]:
            raise ValueError("manifest/object columns mismatch")
        total += len(frame)
        dates = cast(pd.Series, frame["Date"])
        first, last = _date_text(dates.min()), _date_text(dates.max())
        start_date = first if start_date is None else min(start_date, first)
        end_date = last if end_date is None else max(end_date, last)
        sources.update(record["coverage"]["sources"])
        if collect_frames:
            frames.append(frame)
        # Unique object codes plus per-object key validation prove global key
        # uniqueness without retaining or concatenating the whole generation.
        del frame, dates
    coverage = manifest["coverage"]
    expected = {
        "symbols": len(codes),
        "rows": total,
        "start_date": start_date,
        "end_date": end_date,
        "sources": sorted(sources),
    }
    if coverage != expected:
        raise ValueError("manifest coverage mismatch")
    return manifest, frames


def verify_manifest(manifest_path: Path) -> dict[str, Any]:
    """Verify one object at a time, without requiring the current producer version."""
    manifest, _ = _verified(manifest_path)
    return {
        "schema_version": manifest["schema_version"],
        "contract_version": manifest["contract_version"],
        "price_basis": manifest["price_basis"],
        **manifest["coverage"],
        "manifest_sha256": manifest_path.stem,
    }


def load_features(manifest_path: Path) -> pd.DataFrame:
    """Verify every retained input/output/identity, then return the pinned rows."""
    _, frames = _verified(manifest_path, collect_frames=True)
    return pd.concat(frames, ignore_index=True).sort_values(KEYS, kind="stable").reset_index(drop=True)


def _publish(staging: Path, destination: Path) -> None:
    _safe(destination)
    if destination.exists():
        raise ValueError(f"publication target already exists: {destination}")
    staging.rename(destination)


def build_cache(frame: pd.DataFrame, output_dir: Path, *, price_basis: str) -> BuildResult:
    """Compute only changed symbols; publish a verified immutable generation last."""
    if not isinstance(price_basis, str) or not price_basis.strip():
        raise ValueError("explicit nonempty price_basis required")
    normalized = components.normalize_input(frame)
    if normalized.empty:
        raise ValueError("empty input is not a cache generation")
    root = _safe(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock = _safe(root / "build.lock")
    token = uuid.uuid4().hex
    lock_contents = _canonical({"token": token, "pid": os.getpid()}).decode("utf-8")
    # Failure to acquire occurs before try/finally: never remove another lock.
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(lock_contents)
        stream.flush()
        os.fsync(stream.fileno())
    computed = reused = written = 0
    try:
        for name in ("objects", "manifests", "staging"):
            _safe(root / name).mkdir(exist_ok=True)
        runtime, implementation = _runtime(), _implementation()
        entries = []
        output_columns: list[str] | None = None
        for code, group in normalized.groupby("Code", sort=True):
            symbol = cast(pd.DataFrame, group).reset_index(drop=True)
            code = str(code)
            identity = {
                "contract_version": components.CONTRACT_VERSION,
                "price_basis": price_basis,
                "runtime": runtime,
                "implementation": implementation,
                "input_schema": _schema(symbol),
                "normalized_input_digest": _input_digest(symbol),
            }
            object_id = _digest(identity)
            destination = _safe(root / "objects" / object_id)
            if destination.exists():
                record, features = _verify_object(root, object_id)
                reused += 1
            else:
                staging = root / "staging" / uuid.uuid4().hex
                staging.mkdir()
                symbol.to_parquet(staging / "input.parquet", index=False)
                features = components.compute_components(symbol)
                _keys(features, code)
                expected_columns = list(dict.fromkeys([*KEYS, "price_valid", *components.FEATURE_COLUMNS]))
                if list(features.columns) != expected_columns:
                    raise ValueError("computed feature columns do not match contract")
                features.to_parquet(staging / "features.parquet", index=False)
                record = {
                    "schema_version": OBJECT_VERSION,
                    "object_id": object_id,
                    "identity": identity,
                    "normalized_input_digest": identity["normalized_input_digest"],
                    "code": code,
                    "rows": len(symbol),
                    "coverage": _coverage(symbol),
                    "input_schema": _schema(symbol),
                    "features_schema": _schema(features),
                    "input_sha256": _file_digest(staging / "input.parquet"),
                    "features_sha256": _file_digest(staging / "features.parquet"),
                }
                _write_json(staging / "object.json", record)
                _verify_object(root, object_id, staging=staging)
                _publish(staging, destination)
                written += sum((destination / name).stat().st_size for name in ("input.parquet", "features.parquet", "object.json"))
                computed += 1
            output_columns = list(features.columns)
            entries.append({
                "object_id": object_id,
                "path": f"objects/{object_id}/object.json",
                "sha256": _file_digest(destination / "object.json"),
                "code": code,
                "rows": record["rows"],
                "coverage": record["coverage"],
            })
        coverage = _coverage(normalized)
        manifest = {
            "schema_version": MANIFEST_VERSION,
            "contract_version": components.CONTRACT_VERSION,
            "price_basis": price_basis,
            "runtime": runtime,
            "implementation": implementation,
            "definitions": components.definition_metadata(),
            "columns": {"input": list(normalized.columns), "features": output_columns},
            "objects": entries,
            "coverage": {"symbols": len(entries), **coverage},
        }
        manifest_path = root / "manifests" / f"{_digest(manifest)}.json"
        if manifest_path.exists():
            verify_manifest(manifest_path)
        else:
            staging_manifest = root / "staging" / f"{uuid.uuid4().hex}.json"
            _write_json(staging_manifest, manifest)
            _publish(staging_manifest, manifest_path)
            written += manifest_path.stat().st_size
            verify_manifest(manifest_path)
        LOGGER.info("Technical cache computed=%d reused=%d bytes_written=%d manifest=%s", computed, reused, written, manifest_path)
        return BuildResult(manifest_path, computed, reused, written)
    finally:
        if lock.exists() and not lock.is_symlink() and _safe(lock).read_text(encoding="utf-8") == lock_contents:
            lock.unlink()
        else:
            LOGGER.warning("Build lock ownership changed; preserved %s", lock)
