# SPDX-License-Identifier: Apache-2.0
"""AIPerf DataExporterProtocol implementation; opt-in and deferred."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Settings
from .report import invalidate_bundle, write_bundle

if TYPE_CHECKING:
    from aiperf.exporters.exporter_config import ExporterConfig, FileExportInfo

LOGGER = logging.getLogger(__name__)
CONFIG_ENV = "AIPERF_SGLANG_METRICS_CONFIG"


class SGLangMetricsExporter:
    """Export a completed run without changing request scheduling or collection.

    Deferred exporters run after native artifact export. A report error is logged
    by AIPerf without discarding benchmark results or affecting measured latency.
    """

    is_deferred = True

    def __init__(self, exporter_config: ExporterConfig) -> None:
        from aiperf.common.exceptions import DataExporterDisabled

        self.config_path = os.environ.get(CONFIG_ENV)
        if not self.config_path:
            raise DataExporterDisabled(f"Set {CONFIG_ENV} to enable SGLang metric reports")
        self.config = exporter_config
        self.directory = Path(exporter_config.cfg.artifacts.artifact_directory) / "sglang-metrics"

    def get_export_info(self) -> FileExportInfo:
        """Advertise the actual additional report in AIPerf's export log."""
        from aiperf.exporters.exporter_config import FileExportInfo

        return FileExportInfo(export_type="SGLang P/D Metrics Report", file_path=self.directory / "report.html")

    async def export(self) -> None:
        """Read completed in-memory summaries, never race another exporter's file."""
        from aiperf.common.path_safety import safe_read_template_path

        await invalidate_bundle(self.directory)
        text = await asyncio.to_thread(safe_read_template_path, self.config_path)
        if text is None:
            raise ValueError("SGLang report configuration is missing, unsafe or unreadable")
        settings = Settings.from_text(text)
        results = self.config.server_metrics_results
        source = await asyncio.to_thread(results.model_dump, mode="json") if results is not None else {}
        report = await write_bundle(source, settings, self.directory)
        if report["status"] != "complete":
            LOGGER.warning("SGLang metrics report has data-quality warnings; inspect %s", self.directory / "report.html")
