# SPDX-License-Identifier: Apache-2.0
"""Exercise the exporter contract offline and, when installed, actual AIPerf models."""

import asyncio
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import orjson
import pytest

from aiperf_sglang_metrics.demo import DEMO_CONFIG, demo_source
from aiperf_sglang_metrics.exporter import CONFIG_ENV, SGLangMetricsExporter


@pytest.fixture
def api_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimal protocol doubles, not an AIPerf runtime integration test."""
    class Disabled(Exception):
        pass

    @dataclass
    class FileInfo:
        export_type: str
        file_path: Path

    modules = {
        "aiperf.common.exceptions": {"DataExporterDisabled": Disabled},
        "aiperf.exporters.exporter_config": {"FileExportInfo": FileInfo},
        "aiperf.common.path_safety": {
            "safe_read_template_path": lambda value: Path(value).read_text()
            if Path(value).is_file() else None,
        },
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delenv(CONFIG_ENV, raising=False)


def test_exporter_disabled_unless_opted_in(api_contract: None) -> None:
    with pytest.raises(Exception, match="enable SGLang"):
        SGLangMetricsExporter(SimpleNamespace())


def test_exporter_uses_completed_models_and_run_output(
    api_contract: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(DEMO_CONFIG)
    monkeypatch.setenv(CONFIG_ENV, str(config_path))
    calls = []

    class Completed:
        def model_dump(self, *, mode: str) -> dict:
            calls.append(mode)
            return demo_source()

    cfg = SimpleNamespace(artifacts=SimpleNamespace(artifact_directory=tmp_path / "run-7"))
    exporter = SGLangMetricsExporter(SimpleNamespace(cfg=cfg, server_metrics_results=Completed()))
    assert exporter.is_deferred is True
    info = exporter.get_export_info()
    assert info.file_path == tmp_path / "run-7/sglang-metrics/report.html"
    asyncio.run(exporter.export())
    assert calls == ["json"]
    assert info.file_path.is_file()
    manifest = orjson.loads((info.file_path.parent / "manifest.json").read_bytes())
    assert manifest["benchmark_id"] == "synthetic-demo"


def test_missing_server_metrics_produces_explicit_partial_report(
    api_contract: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(DEMO_CONFIG)
    monkeypatch.setenv(CONFIG_ENV, str(config_path))
    cfg = SimpleNamespace(artifacts=SimpleNamespace(artifact_directory=tmp_path))
    exporter = SGLangMetricsExporter(SimpleNamespace(cfg=cfg, server_metrics_results=None))
    asyncio.run(exporter.export())
    data = orjson.loads((tmp_path / "sglang-metrics/metrics.json").read_bytes())
    assert data["status"] == "partial"
    assert not data["series"]


def test_actual_aiperf_model_and_plugin_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    if importlib.util.find_spec("aiperf") is None:
        pytest.skip("Requires the repository AIPerf runtime; contract doubles do not replace this test")
    from aiperf.common.models.server_metrics_models import ServerMetricsResults
    from aiperf.exporters.exporter_config import ExporterConfig
    from aiperf.plugin import plugins

    model = ServerMetricsResults.model_validate(demo_source())
    plugin = plugins.get_class("data_exporter", "sglang_metrics_report")
    assert plugin is SGLangMetricsExporter
    config_path = tmp_path / "config.yaml"
    config_path.write_text(DEMO_CONFIG)
    monkeypatch.setenv(CONFIG_ENV, str(config_path))
    cfg = SimpleNamespace(artifacts=SimpleNamespace(artifact_directory=tmp_path))
    exporter = plugin(ExporterConfig(results=None, cfg=cfg, telemetry_results=None,
                                     server_metrics_results=model))
    asyncio.run(exporter.export())
    assert exporter.get_export_info().file_path.exists()


def test_invalid_configuration_removes_previous_report(
    api_contract: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    output = tmp_path / "sglang-metrics"
    output.mkdir()
    (output / "report.html").write_text("STALE_SUCCESS")
    (output / "manifest.json").write_text('{"status":"complete"}')
    config_path = tmp_path / "config.yaml"
    config_path.write_text("schema_version: 999")
    monkeypatch.setenv(CONFIG_ENV, str(config_path))
    cfg = SimpleNamespace(artifacts=SimpleNamespace(artifact_directory=tmp_path))
    exporter = SGLangMetricsExporter(SimpleNamespace(cfg=cfg, server_metrics_results=None))
    with pytest.raises(ValueError):
        asyncio.run(exporter.export())
    assert not (output / "manifest.json").exists()
    assert not (output / "report.html").exists()
