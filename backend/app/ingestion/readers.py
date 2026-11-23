"""Source readers: every supported input becomes one or more ``RawGrid`` objects.

A RawGrid is a rectangular cell matrix plus merged ranges, cell number formats and
provenance. Readers do no interpretation beyond decoding the container format.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from app.core.errors import IngestionError
from app.ingestion.parsers import normalize_text

MAX_CELLS = 20_000_000  # safety guard against decompression bombs / absurd sheets


@dataclass
class RawGrid:
    name: str
    cells: list[list[Any]]
    origin: str
    merged: list[tuple[int, int, int, int]] = field(default_factory=list)  # 0-based inclusive r1,c1,r2,c2
    formats: list[list[str | None]] | None = None
    row_offset: int = 0  # original (0-based) row index of cells[0]
    col_offset: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def n_rows(self) -> int:
        return len(self.cells)

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.cells), default=0)

    def rect(self) -> "RawGrid":
        w = self.n_cols
        for r in self.cells:
            if len(r) < w:
                r.extend([None] * (w - len(r)))
        if self.formats is not None:
            for r in self.formats:
                if len(r) < w:
                    r.extend([None] * (w - len(r)))
        return self


# ---------------------------------------------------------------------------
# Format sniffing
# ---------------------------------------------------------------------------

SUPPORTED_KINDS = {"xlsx", "xls", "csv", "tsv", "json", "pdf", "image", "text"}
EXT_KIND = {
    ".xlsx": "xlsx", ".xlsm": "xlsx", ".xls": "xls", ".csv": "csv", ".tsv": "tsv",
    ".tab": "tsv", ".txt": "text", ".json": "json", ".pdf": "pdf",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image",
}


def sniff_kind(data: bytes, filename: str | None) -> str:
    """Determine the source kind from magic bytes, falling back to the extension.
    Raises IngestionError for unsupported or mismatching content."""
    ext = ""
    if filename and "." in filename:
        ext = filename[filename.rfind("."):].lower()
    head = data[:16]
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile as e:
            raise IngestionError("File looks like a ZIP container but is corrupt.") from e
        if "xl/workbook.xml" in names:
            return "xlsx"
        raise IngestionError("ZIP archives other than Excel workbooks are not supported.")
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "xls"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head[:4] == b"GIF8" or (
        head[:4] == b"RIFF" and data[8:12] == b"WEBP"
    ):
        return "image"
    if b"\x00" in data[:4096] and not (head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff")):
        raise IngestionError("Unsupported binary file format.")
    # text-like
    if ext in (".json",):
        return "json"
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    if ext not in (".csv", ".tsv", ".tab", ".txt") and stripped in (b"{", b"["):
        return "json"
    kind = EXT_KIND.get(ext, "text")
    if kind in ("xlsx", "xls", "pdf", "image"):
        raise IngestionError(f"File extension {ext} does not match its content.")
    return kind


def decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        import chardet

        guess = chardet.detect(data[:200_000])
        enc = guess.get("encoding") or "cp1252"
        return data.decode(enc, errors="replace")
    except (LookupError, ImportError):
        return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------


def read_xlsx(data: bytes, name: str = "workbook") -> list[RawGrid]:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, rich_text=False)
    except Exception as e:  # openpyxl raises many types for corrupt files
        raise IngestionError(f"Could not open Excel workbook: {e}") from e
    grids: list[RawGrid] = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible" and ws.max_row <= 1 and ws.max_column <= 1:
            continue
        max_r, max_c = ws.max_row, ws.max_column
        if max_r * max_c > MAX_CELLS:
            raise IngestionError(f"Sheet '{ws.title}' is too large ({max_r}×{max_c}).")
        cells: list[list[Any]] = []
        fmts: list[list[str | None]] = []
        for row in ws.iter_rows(min_row=1, max_row=max_r, max_col=max_c):
            cells.append([c.value for c in row])
            fmts.append([c.number_format if c.value is not None else None for c in row])
        merged = [
            (m.min_row - 1, m.min_col - 1, m.max_row - 1, m.max_col - 1) for m in ws.merged_cells.ranges
        ]
        # openpyxl leaves merged non-anchor cells empty: fill them with the anchor value
        for r1, c1, r2, c2 in merged:
            v = cells[r1][c1] if r1 < len(cells) and c1 < len(cells[r1]) else None
            f = fmts[r1][c1] if r1 < len(fmts) and c1 < len(fmts[r1]) else None
            for r in range(r1, min(r2, len(cells) - 1) + 1):
                for c in range(c1, min(c2, len(cells[r]) - 1) + 1):
                    if (r, c) != (r1, c1):
                        cells[r][c] = v
                        fmts[r][c] = f
        grids.append(
            RawGrid(
                name=ws.title, cells=cells, formats=fmts, merged=merged, origin="xlsx",
                meta={"hidden": ws.sheet_state != "visible"},
            ).rect()
        )
    if not grids:
        raise IngestionError("Workbook contains no worksheets.")
    return grids


def read_xls(data: bytes, name: str = "workbook") -> list[RawGrid]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data, formatting_info=True)
    except Exception as e:
        try:
            book = xlrd.open_workbook(file_contents=data)
        except Exception:
            raise IngestionError(f"Could not open legacy .xls workbook: {e}") from e
    grids = []
    for sh in book.sheets():
        cells: list[list[Any]] = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                    except Exception:
                        row.append(cell.value)
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    row.append(None)
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    row.append(bool(cell.value))
                elif cell.ctype == xlrd.XL_CELL_ERROR:
                    row.append(None)
                elif cell.ctype == xlrd.XL_CELL_NUMBER and float(cell.value).is_integer():
                    row.append(int(cell.value))
                else:
                    row.append(cell.value)
            cells.append(row)
        merged = [(rlo, clo, rhi - 1, chi - 1) for rlo, rhi, clo, chi in getattr(sh, "merged_cells", [])]
        for r1, c1, r2, c2 in merged:
            v = cells[r1][c1]
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    cells[r][c] = v
        grids.append(RawGrid(name=sh.name, cells=cells, merged=merged, origin="xls").rect())
    return grids


# ---------------------------------------------------------------------------
# Delimited text
# ---------------------------------------------------------------------------


def _sniff_delimiter(text: str) -> str | None:
    sample = "\n".join(text.splitlines()[:50])
    lines = [ln for ln in sample.splitlines() if ln.strip()]
    best, best_score = None, 0.0
    for d in ("\t", ",", ";", "|"):
        counts = [ln.count(d) for ln in lines]
        if not counts or max(counts) == 0:
            continue
        mode = max(set(counts), key=counts.count)
        if mode == 0:
            continue
        consistency = counts.count(mode) / len(counts)
        score = consistency * (1 + min(mode, 20) / 20) + (0.3 if d == "\t" else 0)
        if score > best_score:
            best, best_score = d, score
    return best


def read_delimited(text: str, name: str, origin: str, delimiter: str | None = None) -> list[RawGrid]:
    delimiter = delimiter or _sniff_delimiter(text)
    if delimiter is None:
        # single column or whitespace-aligned text
        lines = text.splitlines()
        if any(re.search(r"\S {2,}\S", ln) for ln in lines[:20]):
            rows = [re.split(r" {2,}", ln.strip()) if ln.strip() else [] for ln in lines]
        else:
            rows = [[ln] if ln.strip() else [] for ln in lines]
    else:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    cells = [[(v if v != "" else None) for v in r] for r in rows]
    if not any(any(v is not None for v in r) for r in cells):
        raise IngestionError("The text contains no data.")
    return [RawGrid(name=name, cells=cells, origin=origin, meta={"delimiter": delimiter}).rect()]


def read_markdown_table(text: str, name: str) -> list[RawGrid] | None:
    lines = [ln.strip() for ln in text.splitlines()]
    tbl = [ln for ln in lines if ln.startswith("|") and ln.endswith("|")]
    if len(tbl) < 2 or len(tbl) < 0.6 * len([ln for ln in lines if ln]):
        return None
    rows = []
    for ln in tbl:
        parts = [p.strip() for p in ln.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", p) for p in parts if p):
            continue
        rows.append([p if p else None for p in parts])
    return [RawGrid(name=name, cells=rows, origin="text", meta={"format": "markdown"}).rect()]


def read_text(text: str, name: str = "Pasted data") -> list[RawGrid]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    md = read_markdown_table(text, name)
    if md:
        return md
    return read_delimited(text, name, "text")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _flatten(obj: dict[str, Any], prefix: str = "", out: dict[str, Any] | None = None, depth: int = 0) -> dict[str, Any]:
    out = {} if out is None else out
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict) and depth < 5:
            _flatten(v, key, out, depth + 1)
        elif isinstance(v, (list, dict)):
            out[key] = json.dumps(v, ensure_ascii=False)
        else:
            out[key] = v
    return out


def _records_to_grid(records: list[Any], name: str) -> RawGrid:
    if all(isinstance(r, dict) for r in records):
        flat = [_flatten(r) for r in records]
        header: dict[str, None] = {}
        for r in flat:
            for k in r:
                header.setdefault(k, None)
        cols = list(header)
        body = [[r.get(k) for k in cols] for r in flat]
        return RawGrid(name=name, cells=[cols, *body], origin="json", meta={"json_header": True}).rect()
    if all(isinstance(r, list) for r in records):
        return RawGrid(name=name, cells=[list(r) for r in records], origin="json").rect()
    return RawGrid(name=name, cells=[["value"], *[[r] for r in records]], origin="json", meta={"json_header": True})


def read_json(text: str, name: str = "data") -> list[RawGrid]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # JSON Lines
        try:
            obj = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        except json.JSONDecodeError as e:
            raise IngestionError(f"Invalid JSON: {e}") from e
    grids: list[RawGrid] = []

    def walk(o: Any, path: str, depth: int) -> None:
        if depth > 4:
            return
        if isinstance(o, list) and o and all(isinstance(x, (dict, list)) for x in o):
            grids.append(_records_to_grid(o, path or name))
        elif isinstance(o, dict):
            # columnar form {"a": [..], "b": [..]}
            vals = list(o.values())
            if vals and all(isinstance(v, list) for v in vals) and len({len(v) for v in vals}) == 1 and not any(
                isinstance(x, (dict, list)) for v in vals for x in v
            ):
                header = list(o.keys())
                body = [list(r) for r in zip(*vals)]
                grids.append(RawGrid(name=path or name, cells=[header, *body], origin="json", meta={"json_header": True}))
                return
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else str(k), depth + 1)

    walk(obj, "", 0)
    if not grids:
        if isinstance(obj, dict):
            grids.append(_records_to_grid([obj], name))
        else:
            raise IngestionError("No tabular structure found in JSON.")
    return grids


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def read_pdf(data: bytes, name: str = "document") -> list[RawGrid]:
    import pdfplumber

    grids: list[RawGrid] = []
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:
        raise IngestionError(f"Could not open PDF: {e}") from e
    with pdf:
        if len(pdf.pages) > 500:
            raise IngestionError("PDF has more than 500 pages.")
        prev: RawGrid | None = None
        for pno, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables() or []
            if not tables:
                tables = page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text"}) or []
                tables = [t for t in tables if len(t) >= 2 and max(len(r) for r in t) >= 2]
            for tno, t in enumerate(tables, start=1):
                rows = [[normalize_text(c) for c in r] for r in t]
                rows = [r for r in rows if any(v is not None for v in r)]
                if not rows:
                    continue
                width = max(len(r) for r in rows)
                # continuation of previous page's table: same width & repeated (or no) header
                if prev is not None and tno == 1 and prev.n_cols == width and prev.meta.get("last_page") == pno - 1:
                    if rows[0] == prev.cells[0]:
                        rows = rows[1:]
                        prev.meta.setdefault("repeated_headers_removed", 0)
                        prev.meta["repeated_headers_removed"] += 1
                    prev.cells.extend(rows)
                    prev.meta["last_page"] = pno
                    prev.meta["pages"].append(pno)
                    prev.rect()
                    continue
                g = RawGrid(
                    name=f"Page {pno} table {tno}", cells=rows, origin="pdf",
                    meta={"pages": [pno], "last_page": pno},
                ).rect()
                grids.append(g)
                prev = g
    if not grids:
        raise IngestionError(
            "No tables were found in the PDF. Scanned PDFs need OCR: upload page images instead."
        )
    return grids


# ---------------------------------------------------------------------------
# Images (OCR)
# ---------------------------------------------------------------------------

ImageOCR = Callable[[bytes, str], list[list[str | None]]]


def read_image(data: bytes, name: str, ocr: ImageOCR | None) -> list[RawGrid]:
    if ocr is None:
        raise IngestionError(
            "Reading tables from images requires OCR. Configure ANTHROPIC_API_KEY to enable "
            "vision-based table transcription."
        )
    rows = ocr(data, name)
    if not rows:
        raise IngestionError("No table could be read from the image.")
    return [
        RawGrid(
            name=name, cells=rows, origin="image",
            meta={"transcribed_by": "ai_vision", "warning": "Values were transcribed from an image by AI OCR; verify against the original."},
        ).rect()
    ]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def read_source(data: bytes, filename: str | None, kind: str | None = None, ocr: ImageOCR | None = None) -> tuple[str, list[RawGrid]]:
    kind = kind or sniff_kind(data, filename)
    base = (filename or "data").rsplit(".", 1)[0]
    if kind == "xlsx":
        return kind, read_xlsx(data, base)
    if kind == "xls":
        return kind, read_xls(data, base)
    if kind == "pdf":
        return kind, read_pdf(data, base)
    if kind == "image":
        return kind, read_image(data, base, ocr)
    text = decode_text(data)
    if kind == "json":
        return kind, read_json(text, base)
    if kind == "tsv":
        return kind, read_delimited(text, base, "tsv", "\t")
    if kind == "csv":
        return kind, read_delimited(text, base, "csv")
    return "text", read_text(text, base)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
