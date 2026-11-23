"""Phase 2 end-to-end: upload → generate Blueprint → edit → approve → apply → verify.

Usage (servers running): PRISM_WEB_URL=... PRISM_E2E_EMAIL=... PRISM_E2E_PASSWORD=... python e2e/test_phase2_e2e.py [shots_dir]
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
    sample = shots / "calls_phase2.xlsx"
    sample.write_bytes(data)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(WEB)
        page.get_by_label("Email").fill(EMAIL)
        page.get_by_label("Password").fill(PASSWORD)
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_text("What should PRISM read?").or_(page.locator(".src-item")).first).to_be_visible(timeout=15000)
        if page.locator(".src-item").count():
            page.get_by_role("button", name="Add data").click()
        page.locator("input[type=file]").set_input_files(str(sample))
        expect(page.locator(".schema-card")).to_have_count(2, timeout=60000)

        page.get_by_role("tab", name="Blueprint").click()
        expect(page.get_by_role("heading", name="Create a Blueprint")).to_be_visible()
        page.get_by_role("textbox").first.fill("Compare branches on call cost")
        page.screenshot(path=shots / "p2-01-create.png")
        page.get_by_role("button", name="Generate Blueprint").click()
        expect(page.get_by_text("Call center / call log")).to_be_visible(timeout=60000)
        expect(page.get_by_text("AI is not configured")).to_be_visible()
        time.sleep(0.6)
        page.screenshot(path=shots / "p2-02-blueprint.png", full_page=True)

        # rename a column inline
        name = page.get_by_label("Name for ຂໍ້ມູນລູກຄ້າ / ສາຂາ Branch")
        name.fill("Branch ສາຂາ")
        name.press("Enter")
        expect(page.get_by_label("Name for ຂໍ້ມູນລູກຄ້າ / ສາຂາ Branch")).to_have_value("Branch ສາຂາ", timeout=10000)
        # approve the dedupe op explicitly, reject one KPI, then approve the rest
        page.get_by_role("group", name="Decision for Remove 1 exact duplicate row(s)").get_by_title("Approve").click()
        expect(page.get_by_role("group", name="Decision for Remove 1 exact duplicate row(s)").get_by_title("Approve")).to_have_attribute("aria-pressed", "true")
        page.get_by_role("group", name="Decision for Average ຄ່າໂທ (ກີບ)").get_by_title("Reject").click()
        expect(page.get_by_role("group", name="Decision for Average ຄ່າໂທ (ກີບ)").get_by_title("Reject")).to_have_attribute("aria-pressed", "true")
        page.get_by_role("button", name="Show SQL").first.click()
        expect(page.locator("pre.sql").first).to_contain_text("SELECT COUNT(*)")
        page.get_by_role("button", name="Approve all").click()
        expect(page.get_by_role("button", name="Approve all (0)")).to_be_visible(timeout=10000)
        page.screenshot(path=shots / "p2-03-approved.png")

        page.get_by_role("button", name="Apply approved items").click()
        expect(page.get_by_text("Applied —")).to_be_visible(timeout=60000)
        page.screenshot(path=shots / "p2-04-applied.png")

        with page.expect_download() as dl:
            page.get_by_role("button", name="↓ Updated workbook").click()
        wb_bytes = Path(dl.value.path()).read_bytes()
        wb = openpyxl.load_workbook(io.BytesIO(wb_bytes))
        assert "KPIs" in wb.sheetnames and "Blueprint" in wb.sheetnames, wb.sheetnames
        assert wb["Data"]["C1"].value == "Branch ສາຂາ", wb["Data"]["C1"].value
        assert wb["Data"].max_row == len(expected), wb["Data"].max_row  # one duplicate removed
        kpi_names = [c.value for c in wb["KPIs"]["A"] if c.value]
        assert "Average ຄ່າໂທ (ກີບ)" not in kpi_names  # rejected KPI not exported
        ok, bad = recalc_and_compare(wb_bytes)
        assert not bad, bad[:5]

        page.get_by_role("tab", name="Data").click()
        expect(page.locator("table.grid thead")).to_contain_text("Branch ສາຂາ")
        expect(page.locator("table.grid tbody tr")).to_have_count(len(expected) - 1)
        page.screenshot(path=shots / "p2-05-data-v2.png")
        browser.close()
    real = [e for e in errors if "favicon" not in e]
    assert not real, real
    print(f"PHASE 2 E2E OK — workbook verified ({ok} formulas); screenshots in {shots}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "e2e" / "screenshots")
