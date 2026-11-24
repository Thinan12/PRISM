# PRISM

**Give PRISM any data. PRISM understands it, discovers what matters, structures it, calculates it, and turns it into actionable intelligence.**

PRISM is a self-hosted, multi-user data intelligence platform. Upload a messy spreadsheet, CSV, JSON, PDF table or pasted text, including Lao-language call logs with merged headers, mixed date formats, ₭ amounts and call durations. PRISM detects the tables, types every column, profiles quality deterministically, proposes an editable Blueprint, and produces a professional Excel workbook whose every figure is a live formula checked against PRISM's own computation. Ask questions in Lao or English and get answers with the exact SQL behind them; dashboards are generated from the Blueprint and can be filtered, cross-filtered, edited and exported.

> The LLM is never the calculator. All numbers come from deterministic code (pandas/DuckDB) or from Excel formulas PRISM writes and verifies.

- Architecture & design language: [ARCHITECTURE.md](ARCHITECTURE.md)
- What is genuinely done: [PROGRESS.md](PROGRESS.md)
- Guides: [installation](docs/INSTALL.md) · [deployment](docs/DEPLOYMENT.md) · [backup & recovery](docs/BACKUP.md) · [API](docs/API.md) · [database schema](docs/DATABASE.md) · [known limitations](docs/LIMITATIONS.md)

## Repository layout

```
backend/    FastAPI API, job worker, ingestion/profiling engine, Excel builder, Alembic migrations, tests
frontend/   React + TypeScript web app (Night Observatory design), Caddy config for production
e2e/        Browser end-to-end tests (Playwright)
scripts/    Backup and restore
```

## Quick start (development)

Requirements: Python ≥ 3.12, Node ≥ 20.

```bash
python -m venv .venv && .venv/bin/pip install -e "backend[dev]"      # Windows: .venv\Scripts\pip
cd backend
PRISM_ADMIN_EMAIL=admin@prism.local PRISM_ADMIN_PASSWORD=change-me-now \
  ../.venv/bin/uvicorn app.main:app --port 8765        # API + embedded worker, SQLite in ./var
cd ../frontend && npm install
PRISM_API_URL=http://127.0.0.1:8765 npm run dev          # http://localhost:5173
```

## Tests

```bash
cd backend && ../.venv/bin/pytest            # unit, integration, API, Excel formula verification, 100k benchmark
cd frontend && npm test                      # formatting + interaction tests
# end-to-end (servers running):
PRISM_WEB_URL=http://localhost:5173 PRISM_E2E_EMAIL=… PRISM_E2E_PASSWORD=… python e2e/test_phase1_e2e.py
```

## Production

```bash
cp .env.example .env    # set PRISM_SECRET_KEY, POSTGRES_PASSWORD, admin credentials, PRISM_DOMAIN
docker compose up -d --build
```

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
