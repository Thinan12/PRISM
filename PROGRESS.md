# PRISM — Development Progress

Last updated: 2026-11-24 (Phase 2 + pluggable AI provider). Status reflects what was **executed and verified**, not what exists as code.

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

## Phase 2 — AI Intelligence and Blueprint · ✅ complete (acceptance criterion met)

**Acceptance:** PRISM understands a dataset and produces an editable, validated Blueprint before executing transformations.
**Verified by:** `e2e/test_phase2_e2e.py` (browser: generate → rename → approve/reject → apply → download; 246 workbook formulas verified) and `backend/tests/test_blueprint.py`.

| Capability | Status | Evidence |
|---|---|---|
| Strict Pydantic Blueprint (domain, column semantics, operations, KPIs, relationships, dashboard plan, workbook plan, assumptions; confidence, origin and status on every item) | ✅ | `app/blueprint/schema.py` |
| Rule-based generator (bilingual header glossary, type/profile-driven roles, spelling-variant mappings, dedupe, empty columns, KPIs, measured relationships, dashboard plan) | ✅ | `test_rules_blueprint_on_messy_call_log` |
| Claude interpreter: profile-only bounded payload, masked phones, optional no-values mode, JSON-schema structured output, Pydantic validation, retries with error feedback, refusal handling, server-side fallback, typed API errors | ✅ (mocked API) | `test_payload_*`, `test_ai_*` |
| AI proposals validated against the data (unknown columns, impossible aggregations, absent values, unmeasurable relationships, duplicate names), with recorded warnings | ✅ | `test_ai_interpretation_is_validated_against_data` |
| Rule-based fallback when AI is missing, fails, refuses or returns invalid JSON | ✅ | `test_ai_failures_fall_back`, `test_ai_persistently_invalid_falls_back_to_rules`, `test_ai_not_configured` |
| KPIs as declarative specs → parameterized DuckDB SQL with provenance; values checked against pandas | ✅ | `test_kpi_values_are_deterministic_and_correct`, `test_kpi_validation_and_sql_safety` |
| Edit API: approve / reject / rename / remove / modify / add KPI / approve all; invalid edits → 422; drafts only (409 after apply) | ✅ | `test_blueprint_api_full_flow` |
| Apply: approved ops only → new immutable dataset versions, lineage of removed rows, renamed labels, KPIs re-evaluated, workbook with KPIs + Blueprint sheets | ✅ | same + E2E |
| Stale-blueprint protection (data changed since generation) | ✅ | `test_stale_blueprint_cannot_be_applied` |
| Roles, tenant isolation and audit events for blueprints | ✅ | `test_blueprint_tenant_isolation`, audit assertions |
| Blueprint canvas UI (domain, KPI cards with SQL, operations, columns with inline rename/role, relationships, dashboard plan, assumptions; Nebula = AI-inferred) | ✅ | E2E + screenshots reviewed |
| Live Claude API call | ⚠️ **not verified** — no API credentials on the development machine | |

### Phase 2.1 — pluggable AI provider (2026-11-24) · ✅

| Capability | Status | Evidence |
|---|---|---|
| `PRISM_AI_PROVIDER` = auto / ollama / openai_compatible / anthropic / none; same prompt, schema, validation and fallback for all | ✅ | `test_provider_selection`, `test_ollama_*`, `test_openai_compatible_*` |
| Ollama: schema-constrained decoding with array/string bounds (no runaway output), context window sized per request, fresh retry after a cut-off answer, total time budget | ✅ | `test_ollama_provider`, `test_ollama_truncated_output_restarts_fresh`, `test_ollama_context_size` |
| OpenAI-compatible (DeepSeek, OpenRouter, LM Studio, vLLM): json_schema or json_object mode, bearer key, error mapping | ✅ (mocked HTTP) | `test_openai_compatible_provider`, `test_openai_compatible_bad_key_falls_back` |
| **Live local model**: `qwen2.5:7b` via Ollama on the CPU-only dev machine produced a valid interpretation of the messy Lao call log and an `engine=ai` Blueprint | ✅ | 1 attempt, 188 s, 3,332 prompt / 2,094 output tokens (~11 tok/s generation) |
| Extra checks found in the live run: AI names with Lao text not present in the source header are kept out; KPIs filtering on a column without values ("X by Y") are dropped | ✅ | `test_ai_ungrounded_lao_and_breakdown_kpis_rejected` |
| `/ai-status` reports provider, model, reachability and missing model; UI pill shows it; long jobs polled up to an hour | ✅ | `test_ai_status_reports_ollama`, E2E |
| Ollama service in docker-compose (+ one-shot model pull, optional GPU block) | ⚠️ written, **not executed** (no Docker) | `docker-compose.yml` |
| Live DeepSeek / other OpenAI-compatible API | ⚠️ **not verified** — no credentials | |

Defects found by the live runs and fixed: a fixed 32k context window made CPU inference exceed the 900 s timeout (now sized per request: 12k here); the 7B model once looped until the token limit and the retry re-sent the whole cut-off answer (now prevented by grammar bounds; retries start fresh).

Test totals: **backend 127 passed** (100k benchmark: ingest 19.2 s · profile 0.4 s · workbook 8.9 s), **frontend 7 passed**, **E2E Phase 1 (238 formulas) + Phase 2 (246 formulas) passed**.

Defects found and fixed during verification: merged group headers misclassified columns (the leaf header now wins); the upload screen flashed while the source list loaded and could swallow clicks (now gated on the loaded state); confidence bars reused the AI colour; "no blueprint yet" returned 404 (now `null`); stale labels after apply.

## Phase 3 — Analytics and Dashboard Engine · planned
## Phase 4 — Google Sheets Live Intelligence · planned
## Phase 5 — Enterprise System · planned (auth/roles/audit foundations already in place)
