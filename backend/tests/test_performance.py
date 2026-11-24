"""100k-row benchmark: ingestion, profiling and workbook generation on a realistic call log."""

import io
import random
import time
from datetime import datetime, timedelta

import openpyxl
import pytest

from app.excel.builder import WorkbookDataset, WorkbookSource, build_workbook
from app.ingestion.pipeline import ingest
from app.profiling.profiler import profile_table

N = 100_000


def _csv(n: int) -> bytes:
    rnd = random.Random(1)
    branches = ["ວຽງຈັນ", "ຫຼວງພະບາງ", "ສະຫວັນນະເຂດ", "ປາກເຊ", "ອຸດົມໄຊ"]
    lines = ["Call ID,ວັນທີ Date,ສາຂາ Branch,Agent,ເບີໂທ Phone,Duration,ຄ່າໂທ (ກີບ),Status"]
    base = datetime(2024, 1, 1)
    for i in range(n):
        d = base + timedelta(minutes=rnd.randint(0, 525_600))
        sec = rnd.randint(5, 3600)
        lines.append(
            f"{100000 + i},{d:%d/%m/%Y %H:%M},{rnd.choice(branches)},agent{rnd.randint(1, 60)},"
            f"020{rnd.randint(20000000, 99999999)},{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d},"
            f"\"{rnd.randint(1, 50) * 500:,}\",{rnd.choice(['ok', 'missed', 'callback'])}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.mark.slow
def test_100k_rows_pipeline_benchmark(capsys):
    data = _csv(N)
    t0 = time.perf_counter()
    res = ingest(data, "big.csv")
    t1 = time.perf_counter()
    t = res.tables[0]
    p = profile_table(t)
    t2 = time.perf_counter()
    out = build_workbook(
        [WorkbookDataset(name=t.name, columns=t.columns, df=t.clean, profile=p, lineage=t.lineage)],
        WorkbookSource("big.csv", "csv", "0" * 64, len(data)),
    )
    t3 = time.perf_counter()
    assert len(t.clean) == N
    types = {c["label"]: c["logical_type"] for c in t.columns}
    assert types["ວັນທີ Date"] == "datetime" and types["Duration"] == "duration" and types["ເບີໂທ Phone"] == "phone"
    assert p["row_count"] == N and sum(c["parse_failures"] for c in p["columns"]) == 0
    wb = openpyxl.load_workbook(io.BytesIO(out), read_only=True)
    assert wb["Data"].max_row == N + 1
    with capsys.disabled():
        print(f"\n[benchmark] 100k rows: ingest {t1 - t0:.1f}s · profile {t2 - t1:.1f}s · workbook {t3 - t2:.1f}s "
              f"({len(out) / 1e6:.1f} MB)")
    # generous ceilings so CI on modest hardware still catches regressions
    assert t1 - t0 < 120 and t2 - t1 < 60 and t3 - t2 < 120


@pytest.mark.slow
def test_100k_rows_analytics_benchmark(capsys):
    from app.analytics.dashboard import evaluate, generate_content, insights
    from app.analytics.engine import run_query
    from app.analytics.nl import rules_parse
    from app.blueprint.context import DatasetContext

    t = ingest(_csv(N), "big.csv").tables[0]
    ctx = DatasetContext(dataset_id=1, version=1, version_id=1, name=t.name, columns=t.columns, profile=profile_table(t),
                         lineage=t.lineage, df=t.clean)
    t0 = time.perf_counter()
    content, _, notes = generate_content([ctx], None, 1)
    t1 = time.perf_counter()
    data = evaluate(content, {1: ctx}, [])
    t2 = time.perf_counter()
    found = insights(ctx)
    t3 = time.perf_counter()
    parsed = rules_parse("total ຄ່າໂທ by branch", ctx)
    res = run_query(parsed.spec, ctx)
    t4 = time.perf_counter()
    assert len(content.panels) >= 4 and all(d.error is None for d in data), [d.error for d in data]
    assert sum(r[1] for r in res.rows) == t.clean[next(c["key"] for c in t.columns if c.get("unit") == "LAK")].sum()
    with capsys.disabled():
        print(f"\n[benchmark] 100k rows analytics: dashboard plan {t1 - t0:.2f}s · {len(data)} panels {t2 - t1:.2f}s · "
              f"{len(found)} insights {t3 - t2:.2f}s · question {t4 - t3:.2f}s")
    assert t2 - t1 < 10 and t3 - t2 < 10 and t4 - t3 < 5
