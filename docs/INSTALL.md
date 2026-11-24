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

AI (optional — PRISM works without it): with no settings PRISM uses a local [Ollama](https://ollama.com) server at `http://127.0.0.1:11434` if one is running (`ollama pull qwen2.5:7b`). Set `ANTHROPIC_API_KEY` to use Claude instead (also enables image-table transcription), or `PRISM_AI_PROVIDER=openai_compatible` with `PRISM_OPENAI_BASE_URL` / `PRISM_OPENAI_API_KEY` / `PRISM_OPENAI_MODEL` for DeepSeek or another OpenAI-style API. `PRISM_AI_PROVIDER=none` turns AI off. See `.env.example`.

Jobs mode (`PRISM_JOBS_MODE`): `embedded` (default for dev: worker thread inside the API), `external` (production: `python -m app.worker`), `inline` (tests).
