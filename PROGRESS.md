# PRISM — Development Progress

Last updated: 2026-11-24 (Phase 3). Status reflects what was **executed and verified**, not what exists as code.

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

## Phase 3 — Analytics and Dashboard Engine · ✅ complete (acceptance criterion met)

**Acceptance:** a user can ask analytical questions about uploaded data and receive verified results, with the exact SQL available for inspection.
**Verified by:** `e2e/test_phase3_e2e.py` (browser: English and Lao questions → answers checked against pandas → SQL view → generate dashboard → cross-filter → pin answer → edit → export; no horizontal overflow at 390 px) and `backend/tests/test_analytics.py`, `test_analytics_api.py` (the displayed SQL is re-executed independently and must reproduce the returned rows).

| Capability | Status | Evidence |
|---|---|---|
| Declarative QuerySpec → validated, parameterized DuckDB SQL (identifiers from validated keys, bound values, external access off); SQL shown = SQL executed | ✅ | `test_values_are_bound_not_interpolated`, `test_question_answered_with_verifiable_sql` |
| KPI computation, rankings (top/bottom N), comparisons (value sets), filtered measures, shares of total | ✅ | `test_ranking_matches_pandas`, `test_filters_and_filtered_measures_match_pandas`, `test_share_is_of_all_groups_even_when_limited` |
| Trend analysis: time grains (day … year) with period-over-period change (LAG) | ✅ | `test_trend_with_change_matches_pandas` |
| Anomaly detection: robust z-score per period, empty periods filled as zeros, MAD-0 fallback | ✅ | `test_anomaly_detects_planted_spike`, `test_anomaly_on_sparse_series_uses_mean_deviation` |
| Query provenance: dataset + version, matched/total rows, timing, plain-language description generated from the spec | ✅ | all analytics tests |
| Natural-language questions — rule parser (Lao + English, value aliases such as complaints → ຮ້ອງຮຽນ, periods, thresholds); honest "cannot answer" | ✅ | `test_lao_question_with_value_filter`, `test_unanswerable_question_is_honest`, E2E |
| Natural-language questions — AI interpreter (any provider): schema-bounded output, validation errors fed back, fallback to rules; answer text always written by PRISM from the rows | ✅ (mocked HTTP) | `test_ai_question_retries_on_invalid_query`, `test_ai_question_failure_falls_back_to_rules` |
| Live AI question interpretation with local `qwen2.5:7b` (CPU) | ✅ after fixes | first live run: 3/3 queries schema-valid but **wrong** (dropped the complaints filter, no per-branch grouping, summed the row-number column). After ADR-8 (identifier guard, rule-parser grounding check fed back to the model, chronological time series, exact-date-filter check): 3/3 correct in 2–3 attempts, 19–43 s each. With `PRISM_QUESTION_ENGINE=auto` a local model is consulted only when the rule parser cannot map the question |
| Dashboard generation from the applied Blueprint plan (or rules): KPI cards, trend, rankings, part-to-whole, unusual days, filter bar | ✅ | `test_dashboard_from_applied_blueprint`, `test_dashboard_lifecycle` |
| Interactive filtering (date range, categories) and cross-filtering (click a bar/segment; source panel keeps context) | ✅ | `test_dashboard_lifecycle`, E2E (Records 61 → 13 for ປາກເຊ) |
| Dashboard editing: rename, chart type, width, reorder, remove, pin answers; revision-checked (409), every panel re-validated (422) | ✅ | `test_dashboard_lifecycle`, E2E |
| Export: Excel (summary + one sheet per panel with data, provenance and SQL), per-panel CSV | ✅ | `test_dashboard_lifecycle`, E2E |
| Deterministic insights (trend, leader, mix, unusual days) each with SQL | ✅ | `test_insights_have_sql` |
| Roles (viewers ask and view; editors create/edit), tenant isolation, audit (question.asked/answered with SQL, dashboard.created/updated/deleted/exported) | ✅ | API tests |
| Charts: validated categorical palette on the Hull surface, proportional bars (checked in the browser), crosshair + keyboard on lines, table + SQL view for every chart | ✅ | `analytics.test.tsx`, E2E |
| 100k-row analytics benchmark | ✅ | dashboard (9 panels) 0.43 s · 4 insights 0.21 s · question 0.07 s |

Defects found during verification and fixed: bars were capped by the label space so different values drew equal lengths (now proportional; asserted in E2E); two answer cards were shown during the transition; the phone layout overflowed by 122 px (ask-bar selector, date filter); tab strip clipped on phones; line-chart end label collided with the axis; anomaly z-scores were undefined on sparse daily data (MAD = 0); the rule parser grouped by phone because a merged group header ("ການໂທ") was treated as a column name, read "10 minutes?" as seconds, and answered "how many agents" with a breakdown instead of a distinct count.

Test totals: **backend 164 passed** (incl. 100k benchmarks: ingest 22.3 s · profile 0.4 s · workbook 9.1 s · analytics as above), **frontend 15 passed**, **E2E Phase 1 + 2 + 3 passed**.

## Phase 4 — Google Sheets Live Intelligence · planned
## Phase 5 — Enterprise System · planned (auth/roles/audit foundations already in place)
