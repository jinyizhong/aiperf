# SPDX-License-Identifier: Apache-2.0
"""Offline demo or regeneration of this plugin's sanitized source snapshot."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import orjson

from .config import Settings
from .demo import DEMO_CONFIG, demo_source
from .report import write_bundle


async def run(args: argparse.Namespace) -> None:
    """Generate a demo without AIPerf; real snapshot reads use its safety helper."""
    if args.demo:
        source, settings = demo_source(), Settings.from_text(DEMO_CONFIG)
    else:
        from aiperf.common.path_safety import safe_read_template_path

        raw = await asyncio.to_thread(safe_read_template_path, args.source)
        config = await asyncio.to_thread(safe_read_template_path, args.config)
        if raw is None or config is None:
            raise ValueError("Source/config must be readable regular files without symlinks")
        source, settings = orjson.loads(raw), Settings.from_text(config)
    await write_bundle(source, settings, Path(args.output))
    print(Path(args.output) / "report.html")


def main() -> None:
    """CLI entry point; no server or database is started."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Generate explicitly synthetic sample data")
    parser.add_argument("--source", help="Plugin source.json (not an arbitrary AIPerf export)")
    parser.add_argument("--config", help="Report YAML configuration")
    parser.add_argument("--output", required=True, help="Output directory")
    args = parser.parse_args()
    if args.demo and (args.source or args.config):
        parser.error("--demo cannot be combined with --source or --config")
    if not args.demo and not (args.source and args.config):
        parser.error("Use --demo or both --source and --config")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
