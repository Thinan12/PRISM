# PRISM — Architecture

> Status: living technical contract. The original ARCHITECTURE.md referenced by the
> master prompt was not present in the repository, so this document was authored from
> the product specification. Decisions marked **(ADR)** record the reasoning.

## 1. System overview

```
             ┌────────────────────────── Docker Compose ───────────────────────────┐
 Browser ──▶ │ proxy (Caddy) ──▶ web (static React build)                           │
  (PWA)      │        └────────▶ api (FastAPI, uvicorn) ──▶ postgres (metadata)    │
             │                      │   ▲                                           │
             │                      ▼   │ jobs table (SKIP LOCKED)                  │
             │                 worker(s) (python -m app.worker)                      │
             │                 scheduler (python -m app.scheduler)                   │
             │       shared volume: /data  (originals, parquet versions, exports)    │
             └──────────────────────────────────────────────────────────────────────┘
```

| Concern | Choice |
|---|---|
| API | FastAPI + Pydantic v2, OpenAPI at `/api/docs` |
| Metadata DB | PostgreSQL 16 (prod), SQLite (dev/tests) via SQLAlchemy 2 + Alembic |
| Analytical engine | DuckDB over versioned Parquet files |
| Dataset storage | Parquet per dataset version (`raw.parquet` all-text + `clean.parquet` typed) |
| Background jobs | DB-backed job queue (`jobs` table) polled by worker processes **(ADR-1)** |
| Excel | openpyxl, A1-reference formulas (no structured refs) **(ADR-2)** |
| AI | Pluggable provider: local Ollama (default without a key), any OpenAI-compatible API, or Claude; profile-only payloads, strict Pydantic Blueprints, rule-based fallback |
| Frontend | React 18 + TypeScript + Vite, Framer Motion, TanStack Query |
| Deploy | Docker Compose, Caddy reverse proxy; only 80/443 exposed |

**ADR-1 — DB-backed job queue instead of Redis/Celery.** One fewer stateful service on the
company server; Postgres `SELECT … FOR UPDATE SKIP LOCKED` gives safe concurrent workers;
jobs are durable and auditable with the rest of the metadata. Throughput needs (file
ingestion, sync polling) are far below what this pattern handles. In dev/tests
`JOBS_INLINE=true` executes jobs synchronously.

**ADR-2 — A1 references instead of Excel structured references.** Generated workbooks must
also work when uploaded to Google Sheets (Phase 4) and LibreOffice, neither of which fully
supports `Table[Column]` syntax. Excel Tables (ListObjects) are still created for filtering
and styling; formulas reference fixed A1 ranges sized to the data.

## 2. Core principle: deterministic numbers

The LLM never produces numbers. Pipeline:

```
source file ─▶ readers ─▶ RawGrid(s) ─▶ table detection ─▶ header detection
   │                                                        │
   └─ stored immutable (sha256)                              ▼
                                         column parsing (deterministic parsers)
                                                            │
                         raw.parquet (text, lineage) ◀──────┼──────▶ clean.parquet (typed)
                                                            ▼
                                           profiling + quality (pandas/DuckDB)
                                                            │
                             ┌──────────────────────────────┼─────────────────────┐
                             ▼                              ▼                     ▼
                  AI Blueprint (profile only)      Excel workbook          Analytics (DuckDB SQL)
```

Every derived number is produced by Python/DuckDB code or by an Excel formula written
into the workbook; AI output is limited to *interpretation* (names, semantic types, KPI
proposals) and is validated before use.

## 3. Backend modules (`backend/app`)

| Module | Responsibility |
|---|---|
| `core/` | settings, logging, security (password hashing, tokens), errors |
| `db/` | SQLAlchemy engine/session, models, Alembic migrations |
| `ingestion/readers.py` | xlsx/xls/csv/tsv/json/pdf/image/pasted-text → `RawGrid` (cells + merged ranges + provenance) |
| `ingestion/detect.py` | table block detection, title/footer/total row detection, single & multi-row header detection |
| `ingestion/parsers.py` | Lao-aware value parsers: numbers (Lao digits, LAK/₭/ກີບ), dates (mixed formats, Buddhist Era, Lao months, Excel serials), durations, booleans, phones |
| `ingestion/typing.py` | per-column type inference + conversion with failure tracking |
| `profiling/` | column/dataset profiles and quality issues |
| `excel/builder.py` | workbook generation (README, Data, Summary, Data Quality, Lookups, Analysis, Source copies) |
| `blueprint/` | Phase 2: `schema` (Pydantic Blueprint), `rules` (deterministic generator), `ai` (provider-neutral interpreter: Ollama / OpenAI-compatible / Claude), `merge` (validate AI against data), `kpi` (KPI → DuckDB SQL), `executor` (approved ops → new version), `service` (lifecycle) |
| `services/` | orchestration: ingest pipeline, storage, audit |
| `api/` | REST routers (`/api/v1`) |
| `worker.py` | job runner |

### Data model (metadata DB)

- `users`, `workspaces`, `memberships(user, workspace, role ∈ admin|editor|viewer)`
- `sources` — uploaded file / pasted text: sha256, kind, original name, storage path, size
- `datasets` — one per detected table; belongs to workspace + source
- `dataset_versions` — immutable: version no, schema (columns), profile, quality, lineage, parquet paths
- `blueprints` — per source, numbered; status draft → applied | superseded; engine rules | ai; full Blueprint JSON
- `jobs` — type, status (queued|running|succeeded|failed), payload, result, error, attempts
- `artifacts` — generated files (workbooks) linked to dataset version
- `audit_events` — actor, workspace, action, target, details, timestamp

Tenant isolation: every query on workspace-owned rows is filtered by `workspace_id` resolved
from the authenticated membership; cross-workspace IDs return 404.

### Lineage

Every clean row carries `_prism_row` (stable row id), `_source_sheet`, `_source_row`
(1-based original row number). Excluded rows (total/footer rows) are never dropped silently:
they are stored in the version's lineage and written to an *Excluded Rows* sheet.

## 3a. Blueprint (Phase 2)

```
current dataset versions ─▶ rules engine (always) ──────────────┐
          │                                                       ▼
          └─▶ profile-only payload ─▶ AI provider (JSON schema) ─▶ Pydantic ─▶ merge: validate vs data ─▶ Blueprint (draft)
                 (bounded, masked)       retry if invalid          │ failure / refusal / no key → rules only (reason recorded)
                                                                   ▼
          user edits (approve / reject / rename / remove / modify / add KPI) ─▶ apply job
                                                                   ▼
          approved ops only ─▶ new dataset versions (lineage) ─▶ KPIs re-evaluated (DuckDB) ─▶ workbook (KPIs + Blueprint sheets)
```

**ADR-3 — the AI proposes, the data disposes.** The model only *interprets*; its output schema contains no
numbers that PRISM reports. Every proposal is re-checked: unknown columns and values are dropped,
KPIs must compile to valid SQL for the column types, value mappings must reference existing values,
and relationships are kept only when PRISM measures at least 50 % value overlap. Each drop is
recorded as a warning on the Blueprint. Any AI failure falls back to the deterministic rules engine,
so PRISM works without a key. `PRISM_AI_SEND_VALUES=false` restricts the payload to schema and
statistics; phone numbers are always masked.

**ADR-5 — the AI provider is pluggable; local is the default.** `PRISM_AI_PROVIDER` selects
`ollama` (a free open model on the company server — data never leaves it), `openai_compatible`
(DeepSeek, OpenRouter, LM Studio, vLLM…), `anthropic` (Claude `claude-opus-5-5`, structured
outputs, server-side fallback) or `none`; `auto` (default) uses Claude when `ANTHROPIC_API_KEY`
is set and Ollama otherwise. Every provider gets the same system prompt and JSON schema (Ollama
and strict OpenAI-style servers constrain decoding to it), the same Pydantic validation with
error-feedback retries, and the same validation against the data, so a weaker local model can
lower the *quality* of suggestions but not the correctness of anything PRISM reports. Trade-off:
small local models on CPU are slow (minutes) and less insightful than Claude; the provider can be
switched per deployment without code changes. Image-table transcription still requires Claude.

**ADR-4 — KPIs are declarative.** A KPI is `{aggregation, column, filter}` — never free SQL — compiled
to parameterized DuckDB SQL (identifiers quoted from validated keys, values bound as parameters) and
to an equivalent Excel formula. The stored display SQL gives provenance; the workbook's KPI sheet
checks each formula against the DuckDB value.

Operations (`map_values`, `remove_duplicate_rows`, `exclude_column`, `cast_column`) run only when
approved, in a fixed order, and produce a new immutable dataset version; removed rows are listed in
lineage and earlier versions stay readable.

## 4. Localization

UTF-8 everywhere; timezone `Asia/Vientiane`; LAK formatting `#,##0 ₭`; durations stored as
integer seconds and exported as Excel time `[h]:mm:ss`; Lao text cells exported in
*Noto Sans Lao*; date parsing defaults to day-first (Lao convention) unless the column
evidence proves month-first; Buddhist Era years (≥ 2400) are converted (−543).

## 5. Security

- Passwords: scrypt (stdlib) with per-user salt. Tokens: HMAC-signed, expiring.
- Upload validation: extension allow-list + magic-byte sniffing + size limit; files stored
  under content hash, never under user-supplied names.
- SQL (Phase 3): AST-validated read-only SELECTs against a per-request DuckDB connection
  that only has the workspace's views registered.
- Credentials (Phase 4): encrypted at rest with a key from `PRISM_SECRET_KEY`.
- Audit events for uploads, workbook generation, blueprint approvals, sharing, role changes.

## 6. Design language — Night Observatory

| Token | Value | Use |
|---|---|---|
| `--deep-space` | `#060912` | app background |
| `--hull-1` / `--hull-2` / `--hull-3` | `#0C1220` / `#121A2C` / `#1A2439` | panels, raised surfaces, hover |
| `--hull-line` | `#24304A` | hairlines |
| `--starlight` / `--starlight-dim` | `#E8EDF7` / `#8D99B3` | primary / secondary text |
| `--aurora` | `#3EE6B0` | live data, success, sync |
| `--ember` | `#FF8B4D` | warnings, data quality issues |
| `--nebula` | `#A98BFF` | AI-inferred information |
| `--flare` | `#FF5470` | errors / destructive |

Typography: *Bricolage Grotesque* (display), *Schibsted Grotesk* (UI/body),
*Noto Sans Lao* (Lao script, always in the font stack). Motion via Framer Motion, disabled
under `prefers-reduced-motion`.

Layout: left rail (workspaces/navigation) · source explorer · central canvas
(Schema Map → Blueprint → Analytics) · right Inspector · global command bar (Ctrl/⌘-K).

## 7. Phases

See `PROGRESS.md` for the live checklist and acceptance status.
