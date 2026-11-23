# PRISM — Development Progress

Last updated: 2026-11-23. Status reflects what was **executed and verified**, not what exists as code.

## Phase 1 — Data Intelligence Foundation · ✅ complete (acceptance criterion met)

**Acceptance:** a user uploads a messy Excel workbook containing Lao text; PRISM ingests, profiles, cleans and generates a valid Excel workbook.
**Verified by:** `e2e/test_phase1_e2e.py` (real browser against running API + web) and `backend/tests/test_api.py`.

| Capability | Status | Evidence |
|---|---|---|
| XLSX / XLSM ingestion incl. merged cells, hidden sheets | ✅ | `test_ingestion.py::test_messy_*` |
| Legacy XLS incl. merges | ✅ | `test_legacy_xls_with_merges` |
| CSV / TSV (delimiter + encoding sniffing, UTF-8 BOM) | ✅ | `test_csv_semicolon_and_encoding`, `test_tsv_and_pasted_text` |
| JSON / JSON Lines (nested records flattened, columnar form) | ✅ | `test_json_records_nested` |
| PDF tables (multi-page continuation, repeated headers removed) | ✅ | `test_pdf_table` |
| Pasted text (Excel copy/TSV, CSV, Markdown, aligned columns) | ✅ | `test_tsv_and_pasted_text`, API `test_paste_text` |
| Screenshots/photos of tables | ⚠️ implemented, **not verified** | Claude-vision transcription in `app/ai/vision.py`; requires `ANTHROPIC_API_KEY`; without it a clear error is returned (tested) |
| Multiple tables per sheet (stacked, side by side), multiple sheets | ✅ | `test_side_by_side_and_stacked_tables` |
| Title rows, single + two-level merged headers, blank rows inside data | ✅ | `test_messy_call_log_structure`, `test_blank_row_inside_table_is_continuation` |
| Total/subtotal rows detected, excluded, **reconciled** against computed sums | ✅ | `test_messy_profile`, `test_total_mismatch_detected` |
| Lao text (NFC), Lao digits, Lao month names, Buddhist Era years | ✅ | `test_parsers.py` |
| Mixed date formats with per-column day/month order evidence | ✅ | `test_parse_date*`, `test_date_order_evidence` |
| LAK currency (₭, LAK, kip, ກີບ, text and number formats) | ✅ | `test_parse_number`, messy workbook |
| Call durations (hh:mm:ss, mm:ss, `5 ນາທີ 3 ວິນາທີ`, `3m 20s`, Excel time cells) | ✅ | `test_parse_duration` |
| Phones keep leading zeros | ✅ | `test_phone_keeps_leading_zero` |
| Duplicates, missing values, spelling variants, outliers, future dates | ✅ | `test_messy_profile` |
| Unreadable values kept in raw layer, flagged — never dropped | ✅ | `test_parse_failures_are_reported_not_dropped` |
| Source lineage (sheet, source row, header rows, excluded rows) | ✅ | API `test_dataset_detail_rows_and_raw_layer` |
| Dataset versioning (immutable Parquet per version + content hash) | ✅ (v1 creation; new versions arrive with Phase 4 sync) | |
| Excel workbook: README, Data (Table, formats, validation, conditional formatting, lineage + flags), Summary, Analysis (monthly COUNTIFS/SUMIFS + charts, cross-tab), Data Quality, Lookups, Excluded Rows, source sheet copies | ✅ | `test_excel.py` |
| **Every workbook formula independently recalculated = PRISM value** | ✅ (238/238 on the sample) | `tests/excel_verify.py` |
| Auth (scrypt, JWT, token revocation), roles, tenant isolation, audit log | ✅ (foundation for Phase 5) | `test_api.py` |
| Background jobs (DB queue, embedded/external worker) | ✅ | inline in tests; embedded worker in E2E run |
| Web UI: login, workspace shell, source explorer, Schema Map, Inspector, Data (typed/original), Quality, command bar (Ctrl/⌘-K), upload + paste | ✅ | E2E + screenshots reviewed |
| 100k-row benchmark | ✅ | `test_performance.py`: ingest ≈12–19 s · profile <1 s · workbook ≈5–8 s |
| Docker Compose deployment | ⚠️ written, **not executed** (Docker unavailable on the dev machine) | `docker-compose.yml`, Dockerfiles, Caddyfile |
| Alembic migrations | ✅ SQLite upgrade + drift check; PostgreSQL DDL rendered offline, not run against a live server | `alembic/versions/0001_initial_schema.py` |

Test totals at this point: **backend 99 passed**, **frontend 6 passed**, **E2E passed** (238 formulas verified in the downloaded workbook).

## Phase 2 — AI Intelligence and Blueprint · ⏳ next
- [ ] Pydantic Blueprint schema (domain, semantic columns, relationships, KPIs, dashboard plan, workbook plan, quality fixes, confidence, assumptions)
- [ ] Claude client: profile-only payloads (bounded samples), validation + retries, rule-based fallback
- [ ] Blueprint persistence + versioning, approve/reject/rename/remove/modify operations
- [ ] Blueprint execution: approved transformations → new dataset version → workbook
- [ ] Blueprint canvas in UI (Nebula = AI-inferred)
- [ ] Tests incl. AI-unavailable and malformed-AI-output scenarios

## Phase 3 — Analytics and Dashboard Engine · planned
## Phase 4 — Google Sheets Live Intelligence · planned
## Phase 5 — Enterprise System · planned (auth/roles/audit foundations already in place)
