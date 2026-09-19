# SPDX-License-Identifier: Apache-2.0
"""Opt-in offline browser tests. No SGLang or GPU is contacted."""

import os

import pytest

from aiperf_sglang_metrics.config import Settings
from aiperf_sglang_metrics.demo import DEMO_CONFIG, demo_source
from aiperf_sglang_metrics.normalize import build_report
from aiperf_sglang_metrics.report import render_html

pytestmark = pytest.mark.skipif(
    not os.environ.get("AIPERF_SGLANG_BROWSER_TESTS"),
    reason="Set AIPERF_SGLANG_BROWSER_TESTS=1 to run Playwright checks",
)


def test_offline_report_controls_and_security() -> None:
    api = pytest.importorskip("playwright.sync_api")
    errors, requests = [], []
    report = build_report(demo_source(), Settings.from_text(DEMO_CONFIG))
    report["title"] = '</script><script>window.pwned=1</script>'
    with api.sync_playwright() as p:
        executable = os.environ.get("CHROMIUM_EXECUTABLE")
        browser = p.chromium.launch(executable_path=executable, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url))
        page.set_content(render_html(report), wait_until="load")
        page.wait_for_timeout(150)
        assert page.locator(".card").count() == 18
        assert page.locator("#phase").input_value() == "phase-1"
        assert page.evaluate("window.pwned") is None
        page.locator('[data-group="transfer"]').click()
        assert page.locator(".card").count() == 4
        page.locator("#phase").select_option("all")
        assert page.locator(".card").count() == 8
        page.locator("#search").fill("latency")
        assert page.locator(".card").count() == 2
        for stat in ("p50", "p95", "p99", "max", "avg"):
            page.locator("#stat").select_option(stat)
        page.locator("#smooth").select_option("0.35")
        page.locator("#scale").select_option("log")
        page.locator("#clock").select_option("utc")
        page.locator("#theme").click()
        page.locator("#start").evaluate("e => {e.value=500;e.dispatchEvent(new Event('input'))}")
        page.locator("#reset").click()
        assert page.locator("#start").input_value() == "0"
        page.locator("#about").click()
        assert page.locator("dialog").evaluate("e=>e.open")
        page.locator(".about-close").click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(150)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        assert not requests
        browser.close()
