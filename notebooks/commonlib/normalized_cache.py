"""Read-through cache for normalized notebook JSON."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable


def force_renormalize() -> bool:
    value = os.getenv("FORCE_RENORMALIZE", "").strip().lower()
    return value in ("1", "true", "yes")


def cache_disabled() -> bool:
    value = os.getenv("DISABLE_NORMALIZED_CACHE", "").strip().lower()
    return value in ("1", "true", "yes")


def terraform_cache_path(source_path: str) -> str:
    """e.g. plan-tflog.log -> plan-tflog-norm.json (same directory)."""
    source = Path(source_path)
    return str(source.with_name(f"{source.stem}-norm.json"))


def sdk_cache_path(source_path: str) -> str:
    """e.g. plan-tflog.log -> plan-tflog-sdk-norm.json (same directory)."""
    source = Path(source_path)
    return str(source.with_name(f"{source.stem}-sdk-norm.json"))


def cache_is_fresh(source_path: str, cache_path: str) -> bool:
    if not source_path or not cache_path:
        return False
    if not os.path.isfile(source_path) or not os.path.isfile(cache_path):
        return False
    return os.path.getmtime(cache_path) >= os.path.getmtime(source_path)


def load_cache(cache_path: str) -> list:
    with open(cache_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"Normalized cache must be a JSON array: {cache_path}")
    return data


def write_cache(cache_path: str, records: list) -> None:
    with open(cache_path, "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=4)


def _try_load_cache(source_path: str, cache_path: str) -> list | None:
    if cache_disabled() or not cache_path or force_renormalize():
        return None
    if not cache_is_fresh(source_path, cache_path):
        return None
    try:
        records = load_cache(cache_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Normalized cache invalid, rebuilding: {cache_path} ({exc})")
        return None
    print(f"Using normalized cache: {cache_path}")
    return records


def normalize_with_cache(
    source_path: str,
    cache_path: str,
    records: list,
    normalize_fn: Callable[[list], list],
) -> list:
    cached = _try_load_cache(source_path, cache_path)
    if cached is not None:
        return cached

    normalized = normalize_fn(records)
    if cache_path and not cache_disabled():
        write_cache(cache_path, normalized)
        print(f"Wrote normalized cache: {cache_path}")
    return normalized


def load_or_normalize(
    source_path: str,
    cache_path: str,
    read_fn: Callable[[str], list],
    normalize_fn: Callable[[list], list],
) -> list:
    cached = _try_load_cache(source_path, cache_path)
    if cached is not None:
        return cached

    records = read_fn(source_path)
    normalized = normalize_fn(records)
    if cache_path and not cache_disabled():
        write_cache(cache_path, normalized)
        print(f"Wrote normalized cache: {cache_path}")
    return normalized
