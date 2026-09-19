# SPDX-License-Identifier: Apache-2.0
"""Atomic offline artifact generation, off the benchmark event loop."""
from __future__ import annotations
import asyncio
import os
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4
import orjson
from .config import Settings
from .normalize import build_report, clean, snapshot
from .visibility import enrich_report

OWNED_FILES = ("manifest.json", "report.html", "metrics.json", "source.json")


def render_html(report: dict[str, Any]) -> str:
    """Embed inert JSON safely even when labels include a closing script tag."""
    data = orjson.dumps(clean(report)).decode().replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    template = files("aiperf_sglang_metrics").joinpath("report.html").read_text(encoding="utf-8")
    return template.replace("__REPORT_DATA__", data)


def _atomic(path: Path, payload: bytes) -> None:
    """Commit one file only after all bytes are written; refuse symlink targets."""
    if path.is_symlink():
        raise ValueError("Refusing to overwrite a symlink report artifact")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _invalidate(directory: Path) -> None:
    """Remove only owned artifacts before any potentially failing work."""
    for path in (directory, *directory.parents):
        if path.is_symlink():
            raise ValueError("Report directory must not contain symlinks")
    directory.mkdir(parents=True, exist_ok=True)
    for name in OWNED_FILES:
        if (directory / name).is_symlink():
            raise ValueError("Refusing to overwrite a symlink report artifact")
    for name in OWNED_FILES:
        (directory / name).unlink(missing_ok=True)


def _write_bundle(source: dict[str, Any], settings: Settings, directory: Path) -> dict[str, Any]:
    _invalidate(directory)
    report = enrich_report(build_report(source, settings), source)
    safe_source = snapshot(source)
    if "_aiperf_version" in source:
        safe_source["_aiperf_version"] = str(source["_aiperf_version"])
    data = {"metrics.json": orjson.dumps(report, option=orjson.OPT_INDENT_2),
            "source.json": orjson.dumps(safe_source, option=orjson.OPT_INDENT_2),
            "report.html": render_html(report).encode()}
    for name, payload in data.items():
        _atomic(directory / name, payload)
    _atomic(directory / "manifest.json", orjson.dumps({
        "schema_version": "1.1", "status": report["status"], "benchmark_id": report["benchmark_id"],
        "files": list(data), "origin_ns": report["origin_ns"], "end_ns": report["end_ns"],
        "runtime": report["runtime"],
    }, option=orjson.OPT_INDENT_2))
    return report


async def write_bundle(source: dict[str, Any], settings: Settings, directory: Path) -> dict[str, Any]:
    """Generate a complete report after sampling has finished."""
    return await asyncio.to_thread(_write_bundle, source, settings, directory)


async def invalidate_bundle(directory: Path) -> None:
    """Do not leave a stale successful report after configuration validation fails."""
    await asyncio.to_thread(_invalidate, directory)
