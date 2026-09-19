# SPDX-License-Identifier: Apache-2.0
"""Strict configuration and credential-safe display values."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

ROLES = {"prefill", "decode", "transfer", "unknown"}
GROUPS = {"prefill", "decode", "transfer", "kvcache", "other"}


def public_url(url: str) -> str:
    """Strip credentials, queries and fragments from a display-only endpoint."""
    parsed = urlsplit(url)
    host = parsed.hostname or "unknown"
    if ":" in host:
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, host + port, parsed.path.rstrip("/"), "", ""))


def safe_labels(labels: dict[str, Any] | None) -> dict[str, str]:
    """Do not persist common credential-bearing labels in report artifacts."""
    blocked = ("authorization", "password", "secret", "api_key", "apikey", "access_token")
    return {
        str(key): "[redacted]" if any(s in str(key).lower() for s in blocked) else str(value)
        for key, value in sorted((labels or {}).items())
    }


@dataclass(frozen=True, slots=True)
class Settings:
    """Report settings.

    title: Display title, never interpreted as HTML.
    endpoints: Exact sanitized endpoint URL -> role/name mapping.
    metrics: Exact metric-name display overrides (not PromQL or executable code).
    max_series: Fail explicitly before an excessive report is generated.
    max_points: Per-series limit; data is never silently downsampled.
    """

    title: str = "SGLang P/D Metrics"
    endpoints: dict[str, dict[str, str]] = field(default_factory=dict)
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_series: int = 1000
    max_points: int = 20000

    @classmethod
    def from_text(cls, text: str) -> Settings:
        """Reject unknown keys so a typo cannot silently change the experiment."""
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("Report configuration must be a YAML mapping")
        allowed = {"schema_version", "title", "endpoints", "metrics", "max_series", "max_points"}
        if set(data) - allowed:
            raise ValueError(f"Unknown report settings: {sorted(set(data) - allowed)}")
        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise ValueError("Report configuration requires schema_version: 1")
        if not isinstance(data.get("title", "SGLang P/D Metrics"), str):
            raise ValueError("title must be a string")
        raw_endpoints = data.get("endpoints", [])
        if not isinstance(raw_endpoints, list):
            raise ValueError("endpoints must be a list")
        endpoints = {}
        for item in raw_endpoints:
            if not isinstance(item, dict) or set(item) - {"url", "role", "name"}:
                raise ValueError("Each endpoint requires url, role and name only")
            url = str(item.get("url", ""))
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError("Endpoint must be an absolute http(s) /metrics URL")
            if parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError("Endpoint mapping must not contain credentials, query or fragment")
            role = item.get("role")
            if role not in ROLES:
                raise ValueError(f"Unsupported endpoint role: {role}")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Endpoint name must be a non-empty string")
            key = public_url(url)
            if key in endpoints:
                raise ValueError(f"Duplicate endpoint mapping: {key}")
            endpoints[key] = {"role": role, "name": name}
        metrics = data.get("metrics", {})
        if not isinstance(metrics, dict):
            raise ValueError("metrics must be a mapping")
        for name, spec in metrics.items():
            if not isinstance(name, str) or not name:
                raise ValueError("Metric names must be non-empty strings")
            if not isinstance(spec, dict) or set(spec) - {"title", "group", "unit", "scale"}:
                raise ValueError(f"Invalid metric override: {name}")
            if spec.get("group", "other") not in GROUPS:
                raise ValueError(f"Invalid metric group: {name}")
            for field_name in ("title", "unit"):
                if field_name in spec and not isinstance(spec[field_name], str):
                    raise ValueError(f"Metric {field_name} must be a string: {name}")
            scale = spec.get("scale", 1)
            if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
                raise ValueError(f"Metric scale must be finite and positive: {name}")
        for key, default in (("max_series", 1000), ("max_points", 20000)):
            value = data.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100000:
                raise ValueError(f"{key} must be an integer in [1, 100000]")
        return cls(str(data.get("title", "SGLang P/D Metrics")), endpoints, metrics,
                   data.get("max_series", 1000), data.get("max_points", 20000))
