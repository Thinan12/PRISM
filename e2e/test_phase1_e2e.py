"""Phase 1 end-to-end test against a running PRISM (API + web).

Usage:
    PRISM_WEB_URL=http://127.0.0.1:5173 PRISM_E2E_EMAIL=... PRISM_E2E_PASSWORD=... \
    python e2e/test_phase1_e2e.py [screenshot_dir]

Drives a real browser: sign in → upload a messy Lao call-log workbook → Schema Map →
Inspector → Data → Quality → download the generated workbook and verify its contents.
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

import openpyxl
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from tests.excel_verify import recalc_and_compare  # noqa: E402
from tests.fixtures.make_samples import messy_call_log_xlsx  # noqa: E402

WEB = os.environ.get("PRISM_WEB_URL", "http://127.0.0.1:5173")
EMAIL = os.environ.get("PRISM_E2E_EMAIL", "admin@prism.local")
PASSWORD = os.environ.get("PRISM_E2E_PASSWORD", "prism-admin-2026")


def main(shots: Path) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    data, expected = messy_call_log_xlsx()
    sample = shots / "ລາຍງານການໂທ_Q1.xlsx"
    sample.write_bytes(data)
    console_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 960}, accept_downloads=True)
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        page.goto(WEB)
        expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
        page.screenshot(path=shots / "01-login.png")
        page.get_by_label("Email").fill(EMAIL)
        page.get_by_label("Password").fill("wrong-password")
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_role("alert")).to_contain_text("Incorrect")
        page.get_by_label("Password").fill(PASSWORD)
        page.get_by_role("button", name="Sign in").click()

        # empty workspace → upload hero, or the + Add button when data exists
        expect(page.get_by_text("What should PRISM read?").or_(page.locator(".src-item")).first).to_be_visible(timeout=15000)
        if page.locator(".src-item").count():
            page.get_by_role("button", name="Add data").click()
        page.screenshot(path=shots / "02-upload.png")
        page.locator("input[type=file]").set_input_files(str(sample))

        expect(page.get_by_role("tab", name="Schema Map")).to_be_visible(timeout=60000)
        expect(page.locator(".schema-card")).to_have_count(2, timeout=30000)
        time.sleep(1.5)  # let the beam animation finish for the screenshot
        page.screenshot(path=shots / "03-schema-map.png")

        page.get_by_role("button", name="ຄ່າໃຊ້ຈ່າຍ / Duration").first.click()
        insp = page.get_by_label("Inspector")
        expect(insp).to_contain_text("Duration")
        expect(insp).to_contain_text("Total")
        page.get_by_role("button", name="ຂໍ້ມູນລູກຄ້າ / ພະນັກງານ Agent").first.click()
        expect(insp).to_contain_text("spelling variants")
        page.screenshot(path=shots / "04-inspector.png")

        page.get_by_role("tab", name="Data").click()
        expect(page.locator("table.grid tbody tr")).to_have_count(61, timeout=15000)
        expect(page.locator("table.grid")).to_contain_text("₭")
        page.screenshot(path=shots / "05-data.png")
        page.get_by_role("radio", name="Original text").click()
        expect(page.locator("table.grid")).to_contain_text("ນາທີ")

        page.get_by_role("tab", name="Quality").click()
        expect(page.get_by_text("Source totals reconciled")).to_be_visible()
        expect(page.locator("text=matches")).to_have_count(2)
        page.screenshot(path=shots / "06-quality.png", full_page=True)

        page.keyboard.press("Control+k")
        expect(page.get_by_role("dialog", name="Command bar")).to_be_visible()
        page.keyboard.type("Branches")
        page.screenshot(path=shots / "07-command-bar.png")
        page.keyboard.press("Escape")

        with page.expect_download() as dl:
            page.get_by_role("button", name="↓ Excel workbook").click()
        path = dl.value.path()
        wb_bytes = Path(path).read_bytes()
        wb = openpyxl.load_workbook(io.BytesIO(wb_bytes))
        assert wb["Data"].max_row == len(expected) + 1, wb["Data"].max_row
        ok, bad = recalc_and_compare(wb_bytes)
        assert not bad, bad[:5]
        (shots / "downloaded.xlsx").write_bytes(wb_bytes)

        page.set_viewport_size({"width": 800, "height": 900})
        page.get_by_role("tab", name="Schema Map").click()
        time.sleep(0.8)
        page.screenshot(path=shots / "08-narrow.png")
        browser.close()

    # the deliberate wrong-password attempt logs one 401 resource error; anything else is a bug
    real_errors = [e for e in console_errors if "favicon" not in e and "status of 401" not in e]
    assert not real_errors, real_errors
    print(f"E2E OK — workbook {len(wb_bytes):,} bytes, {ok} formulas verified; screenshots in {shots}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "e2e" / "screenshots")
