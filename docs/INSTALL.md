# Installation (development)

1. Install Python ≥ 3.12 and Node ≥ 20.
2. From the repository root:
   ```bash
   python -m venv .venv
   .venv/bin/pip install -e "backend[dev]"        # Windows: .venv\Scripts\pip install -e "backend[dev]"
   cd frontend && npm install && cd ..
   ```
3. Start the API (SQLite + embedded worker; data under `backend/var/`):
   ```bash
   cd backend
   PRISM_ADMIN_EMAIL=admin@prism.local PRISM_ADMIN_PASSWORD=change-me-now ../.venv/bin/uvicorn app.main:app --port 8765 --reload
   ```
   The first start creates the admin user and a "Main workspace". API docs: http://127.0.0.1:8765/api/docs
4. Start the web app: `cd frontend && PRISM_API_URL=http://127.0.0.1:8765 npm run dev`, then open http://localhost:5173.

Optional: set `ANTHROPIC_API_KEY` to enable image-table transcription (and AI Blueprints from Phase 2).

Jobs mode (`PRISM_JOBS_MODE`): `embedded` (default for dev: worker thread inside the API), `external` (production: `python -m app.worker`), `inline` (tests).
