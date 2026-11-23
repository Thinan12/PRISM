"""Independent verification of generated workbooks: every formula is recalculated with
the `formulas` engine and compared with the value PRISM computed and cached."""

from __future__ import annotations

import datetime as dt
import tempfile
import warnings
from pathlib import Path

import openpyxl

EPOCH = dt.datetime(1899, 12, 30)


def _num(x):
    if isinstance(x, dt.datetime):
        return (x - EPOCH).total_seconds() / 86400
    if isinstance(x, dt.timedelta):
        return x.total_seconds() / 86400
    if isinstance(x, dt.time):
        return (x.hour * 3600 + x.minute * 60 + x.second) / 86400
    return x


def recalc_and_compare(data: bytes) -> tuple[int, list[str]]:
    warnings.filterwarnings("ignore")
    import formulas

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "wb.xlsx"
        path.write_bytes(data)
        sol = formulas.ExcelModel().loads(str(path)).finish().calculate()
        wf = openpyxl.load_workbook(path)
        wv = openpyxl.load_workbook(path, data_only=True)
        ok, bad = 0, []
        for ws in wf.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and c.value.startswith("="):
                        got = _num(sol[f"'[wb.xlsx]{ws.title.upper()}'!{c.coordinate}"].value[0, 0])
                        exp = _num(wv[ws.title][c.coordinate].value)
                        if isinstance(exp, (int, float)) and not isinstance(exp, bool):
                            same = isinstance(got, (int, float)) and abs(float(got) - float(exp)) <= 1e-6 * max(1, abs(float(exp)))
                        else:
                            same = str(got) == str(exp)
                        if same:
                            ok += 1
                        else:
                            bad.append(f"{ws.title}!{c.coordinate} {c.value[:80]} → {got!r} (expected {exp!r})")
        return ok, bad
