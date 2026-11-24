"""Phase 3 end-to-end: ask questions (English + Lao) → verified answers with SQL → generate a
dashboard → cross-filter → pin an answer → edit → export. Numbers are checked against an
independent pandas computation on the same file.

Usage (servers running): PRISM_WEB_URL=... PRISM_E2E_EMAIL=... PRISM_E2E_PASSWORD=... python e2e/test_phase3_e2e.py [shots_dir]
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import openpyxl
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("PRISM_SECRET_KEY", "e2e-only-not-used-for-the-server-0123456789")
from app.ingestion.pipeline import ingest  # noqa: E402
from tests.fixtures.make_samples import messy_call_log_xlsx  # noqa: E402

WEB = os.environ.get("PRISM_WEB_URL", "http://127.0.0.1:5173")
EMAIL = os.environ.get("PRISM_E2E_EMAIL", "admin@prism.local")
PASSWORD = os.environ.get("PRISM_E2E_PASSWORD", "prism-admin-2026")


def lak(v: float) -> str:
    return f"{int(round(v)):,} ₭"


def main(shots: Path) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    data, _ = messy_call_log_xlsx()
    sample = shots / "calls_phase3.xlsx"
    sample.write_bytes(data)

    # independent expectations (pandas on PRISM's own parse of the same file)
    df = ingest(data, "calls_phase3.xlsx").tables[0].clean
    cost_by_branch = df.groupby("branch")["col_8"].sum().sort_values(ascending=False)
    complaints = df[df["type"] == "ຮ້ອງຮຽນ"].groupby("branch").size().sort_values(ascending=False)
    pakse_rows = int((df["branch"] == "ປາກເຊ").sum())

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

        page.get_by_role("tab", name="Analytics").click()
        expect(page.get_by_role("heading", name="No dashboard yet")).to_be_visible()

        # 1. English question → verified answer + SQL
        ask = page.get_by_label("Ask a question about the data")
        ask.fill("Total call cost by branch")
        page.get_by_role("button", name="Ask", exact=True).click()
        answer = page.locator(".answer-text")
        top, top_v = cost_by_branch.index[0], cost_by_branch.iloc[0]
        expect(answer).to_contain_text(f"{top} has the highest", timeout=60000)
        expect(answer).to_contain_text(lak(top_v))
        expect(page.locator(".answer .viz-bar-row")).to_have_count(len(cost_by_branch))
        page.locator(".answer").get_by_role("button", name="SQL").click()
        expect(page.locator(".answer pre.sql")).to_contain_text('SUM("col_8")')
        page.screenshot(path=shots / "p3-01-answer-sql.png")

        # 2. Lao question with a value filter
        ask.fill("ສາຂາໃດມີການຮ້ອງຮຽນຫຼາຍທີ່ສຸດ")
        page.get_by_role("button", name="Ask", exact=True).click()
        expect(answer).to_contain_text(f"{complaints.index[0]} has the most records: {complaints.iloc[0]}", timeout=60000)
        expect(page.locator(".answer-how")).to_contain_text("ປະເພດ Type is ຮ້ອງຮຽນ")
        page.screenshot(path=shots / "p3-02-lao-question.png")

        # 3. generate the dashboard
        page.get_by_role("button", name="Generate dashboard").click()
        expect(page.locator(".dash-panel.is-kpi").first).to_be_visible(timeout=60000)
        n = page.locator(".dash-panel").count()
        assert n >= 6, n
        records = page.get_by_role("region", name="Records", exact=True)
        expect(records.locator(".stat-tile-value")).to_have_text(str(len(df)), timeout=30000)
        expect(page.locator(".finding")).to_have_count(4)
        # rendered bar lengths must be proportional to the values (no capped or distorted bars)
        cost_panel = page.locator(".dash-panel").filter(has=page.locator(".viz-bar-row")).first
        widths = cost_panel.locator(".viz-bar").evaluate_all("els => els.map(e => e.getBoundingClientRect().width)")
        for w, v in zip(widths, cost_by_branch.values):
            assert abs(w / widths[0] - v / cost_by_branch.iloc[0]) < 0.02, (widths, list(cost_by_branch.values))
        page.screenshot(path=shots / "p3-03-dashboard.png", full_page=True)

        # 4. cross-filter: click a branch bar → every other panel filters, chip appears
        branch_panel = page.locator(".dash-panel").filter(has=page.locator(".viz-bar-row")).first
        branch_panel.get_by_role("button", name="ປາກເຊ").first.click()
        expect(page.locator(".chip-on")).to_contain_text("ປາກເຊ")
        expect(records.locator(".stat-tile-value")).to_have_text(str(pakse_rows), timeout=30000)
        expect(records.locator(".stat-tile-sub")).to_contain_text(f"from {pakse_rows} of {len(df)} records")
        page.screenshot(path=shots / "p3-04-cross-filter.png", full_page=True)
        page.get_by_role("button", name="Clear filters").click()
        expect(records.locator(".stat-tile-value")).to_have_text(str(len(df)), timeout=30000)

        # 5. pin an answer to the dashboard
        ask.fill("Average call duration per agent")
        page.get_by_role("button", name="Ask", exact=True).click()
        expect(answer).to_contain_text("has the highest Average Duration", timeout=60000)
        page.get_by_role("button", name="＋ Pin to dashboard").click()
        expect(page.locator(".dash-panel")).to_have_count(n + 1, timeout=30000)
        expect(page.get_by_text("rev 2")).to_be_visible()

        # 6. edit: remove a panel, rename the dashboard, save
        page.get_by_role("button", name="Edit", exact=True).click()
        page.get_by_label("Dashboard name").fill("Branch review Q1")
        page.get_by_role("button", name="Remove Unusual days").click()
        page.get_by_role("button", name="Save dashboard").click()
        expect(page.get_by_role("heading", name="Branch review Q1")).to_be_visible(timeout=30000)
        expect(page.locator(".dash-panel")).to_have_count(n)
        expect(page.get_by_text("rev 3")).to_be_visible()

        # 7. panel SQL view
        bar_name = page.locator(".dash-panel").filter(has=page.locator(".viz-bar-row")).first.get_attribute("aria-label")
        bar_panel = page.get_by_role("region", name=bar_name, exact=True)  # stable once the chart is swapped for SQL
        bar_panel.get_by_role("button", name="SQL").click()
        expect(bar_panel.locator("pre.sql")).to_contain_text("GROUP BY 1")
        expect(bar_panel.locator(".sql-meta")).to_contain_text("v1")

        # 8. export
        with page.expect_download() as dl:
            page.get_by_role("button", name="↓ Excel", exact=True).click()
        wb = openpyxl.load_workbook(io.BytesIO(Path(dl.value.path()).read_bytes()))
        assert wb.sheetnames[0] == "Dashboard" and len(wb.sheetnames) == n + 1, wb.sheetnames
        assert wb["Dashboard"]["A1"].value == "Branch review Q1"
        assert "Filters applied: none" in wb["Dashboard"]["A4"].value
        sqls = [c.value for ws in wb.worksheets[1:] for row in ws.iter_rows() for c in row
                if isinstance(c.value, str) and c.value.startswith(("SELECT", "WITH"))]
        assert len(sqls) == n, len(sqls)
        page.screenshot(path=shots / "p3-05-edited.png", full_page=True)

        # 9. phone width: no horizontal scroll on the analytics canvas
        page.set_viewport_size({"width": 390, "height": 900})
        page.wait_for_timeout(400)
        overflow = page.evaluate("() => { const c = document.querySelector('.canvas-body'); return c.scrollWidth - c.clientWidth; }")
        page.screenshot(path=shots / "p3-06-phone.png", full_page=True)
        assert overflow <= 1, f"analytics canvas overflows horizontally by {overflow}px at 390px"
        browser.close()
    real = [e for e in errors if "favicon" not in e]
    assert not real, real
    print(f"PHASE 3 E2E OK — answers and dashboard verified against pandas; screenshots in {shots}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "e2e" / "screenshots")
