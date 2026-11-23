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
