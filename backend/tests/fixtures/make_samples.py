"""Deterministic synthetic sample datasets (no real customer data).

``messy_call_log_xlsx`` reproduces typical problems of hand-maintained Lao call logs:
merged title rows, a two-level merged header, Lao + English text, mixed date formats
(incl. Buddhist Era and Lao month names), LAK amounts written as text, durations in
several notations, phone numbers with leading zeros, blank rows inside the data,
duplicates, spelling variants, missing values, a subtotal and a grand-total row, a note
row and a second small table on another sheet.
"""

from __future__ import annotations

import io
import random
from datetime import datetime, time, timedelta

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

BRANCHES = ["ວຽງຈັນ", "ຫຼວງພະບາງ", "ສະຫວັນນະເຂດ", "ປາກເຊ"]
AGENTS = ["ສົມສັກ", "ບຸນມີ", "ນາງ ແສງດາວ", "Khamla", "Vanh"]
TYPES = ["ສອບຖາມ", "ຮ້ອງຮຽນ", "ຂາຍ", "Support"]
STATUS = ["ສຳເລັດ", "ບໍ່ສຳເລັດ", "ໂທກັບ"]


def _date_variants(d: datetime, i: int):
    k = i % 6
    if k == 0:
        return d  # native Excel date
    if k == 1:
        return d.strftime("%d/%m/%Y")
    if k == 2:
        return d.strftime("%Y-%m-%d")
    if k == 3:
        return f"{d.day:02d}/{d.month:02d}/{d.year + 543}"  # Buddhist Era
    if k == 4:
        lao = ["ມັງກອນ", "ກຸມພາ", "ມີນາ", "ເມສາ", "ພຶດສະພາ", "ມິຖຸນາ", "ກໍລະກົດ", "ສິງຫາ", "ກັນຍາ", "ຕຸລາ", "ພະຈິກ", "ທັນວາ"]
        return f"{d.day} {lao[d.month - 1]} {d.year}"
    return d.strftime("%d-%b-%Y")


def _duration_variants(sec: int, i: int):
    k = i % 5
    if k == 0:
        return time(sec // 3600, (sec % 3600) // 60, sec % 60)  # native time cell
    if k == 1:
        return f"{sec // 60:02d}:{sec % 60:02d}"
    if k == 2:
        return f"{sec // 60} ນາທີ {sec % 60} ວິນາທີ"
    if k == 3:
        return f"{sec // 60}m {sec % 60}s"
    return f"0:{sec // 60:02d}:{sec % 60:02d}"


def call_log_records(n: int = 60, seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    start = datetime(2025, 1, 6)
    recs = []
    for i in range(n):
        d = start + timedelta(days=rnd.randint(0, 80))
        sec = rnd.randint(15, 1500)
        recs.append({
            "no": i + 1,
            "date": d,
            "branch": rnd.choice(BRANCHES),
            "agent": rnd.choice(AGENTS),
            "phone": f"020{rnd.randint(20000000, 99999999)}",
            "type": rnd.choice(TYPES),
            "duration": sec,
            "cost": rnd.choice([500, 1000, 1500, 2000, 2500]) * (1 + sec // 300),
            "status": rnd.choice(STATUS),
        })
    return recs


def messy_call_log_xlsx(n: int = 60, seed: int = 7) -> tuple[bytes, list[dict]]:
    recs = call_log_records(n, seed)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ບັນທຶກການໂທ"
    ws["A1"] = "ລາຍງານການໂທ ປະຈຳໄຕມາດ 1/2025 — Call Report Q1"
    ws.merge_cells("A1:I1")
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = "ບໍລິສັດ ຕົວຢ່າງ ຈຳກັດ"
    ws.merge_cells("A2:I2")
    # two-level header (row 4 group, row 5 sub-headers)
    ws["A4"], ws["B4"], ws["C4"], ws["E4"], ws["G4"], ws["I4"] = "ລ/ດ", "ວັນທີ Date", "ຂໍ້ມູນລູກຄ້າ", "ການໂທ", "ຄ່າໃຊ້ຈ່າຍ", "ສະຖານະ"
    ws.merge_cells("A4:A5"); ws.merge_cells("B4:B5"); ws.merge_cells("C4:D4")
    ws.merge_cells("E4:F4"); ws.merge_cells("G4:H4"); ws.merge_cells("I4:I5")
    ws["C5"], ws["D5"], ws["E5"], ws["F5"], ws["G5"], ws["H5"] = "ສາຂາ Branch", "ພະນັກງານ Agent", "ເບີໂທ Phone", "ປະເພດ Type", "Duration", "ຄ່າໂທ (ກີບ)"
    for c in "ABCDEFGHI":
        for r in (4, 5):
            ws[f"{c}{r}"].font = Font(bold=True)
            ws[f"{c}{r}"].fill = PatternFill("solid", fgColor="DDEBF7")
            ws[f"{c}{r}"].alignment = Alignment(horizontal="center")

    row = 6
    half = n // 2
    expected = []
    for i, rec in enumerate(recs):
        if i == half:
            # subtotal row for the first half, then a blank row
            ws.cell(row, 1, "ລວມ")
            ws.cell(row, 8, f"{sum(r['cost'] for r in recs[:half]):,} ກີບ")
            row += 1
            row += 1  # blank row inside the data
        branch = rec["branch"]
        if i % 11 == 3:
            branch = f"  {branch} "  # whitespace variant
        agent = rec["agent"]
        if agent == "Khamla" and i % 4 == 0:
            agent = "khamla"  # case variant
        ws.cell(row, 1, rec["no"])
        ws.cell(row, 2, _date_variants(rec["date"], i))
        ws.cell(row, 3, branch)
        ws.cell(row, 4, agent)
        ws.cell(row, 5, rec["phone"] if i % 7 else None)  # some missing phones
        ws.cell(row, 6, rec["type"])
        ws.cell(row, 7, _duration_variants(rec["duration"], i))
        if i % 3 == 0:
            ws.cell(row, 8, f"{rec['cost']:,} ກີບ")
        elif i % 3 == 1:
            ws.cell(row, 8, rec["cost"]).number_format = '#,##0 "₭"'
        else:
            ws.cell(row, 8, f"₭{rec['cost']:,}")
        ws.cell(row, 9, rec["status"])
        expected.append(rec)
        row += 1
    # duplicate of the last record (exact duplicate row)
    for c in range(1, 10):
        ws.cell(row, c, ws.cell(row - 1, c).value)
    expected.append(dict(expected[-1]))
    row += 1
    ws.cell(row, 1, "ລວມທັງໝົດ")
    ws.cell(row, 8, sum(r["cost"] for r in expected))
    row += 1
    ws.cell(row, 1, "ໝາຍເຫດ: ຂໍ້ມູນນີ້ສ້າງຂຶ້ນເພື່ອການທົດສອບເທົ່ານັ້ນ")

    ws2 = wb.create_sheet("Branches")
    ws2.append(["Branch code", "ສາຂາ", "Manager", "Opened"])
    ws2.append(["VTE", "ວຽງຈັນ", "ທ່ານ ສີສະຫວາດ", "2015-03-01"])
    ws2.append(["LPB", "ຫຼວງພະບາງ", "ທ່ານ ບົວພັນ", "01/07/2017"])
    ws2.append(["SVK", "ສະຫວັນນະເຂດ", "Ms. Noy", "2018-11-15"])
    ws2.append(["PKZ", "ປາກເຊ", None, "2020-02-20"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), expected
