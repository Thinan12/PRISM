# Deployment (company Linux server)

Requirements: Docker Engine ≥ 24 with the Compose plugin, ~2 GB RAM, disk for uploads. The local AI service needs ~8 GB more RAM and ~5 GB disk for `qwen2.5:7b` (more for larger models; a GPU makes it much faster).

```bash
git clone <repo> prism && cd prism
cp .env.example .env
# edit .env: PRISM_SECRET_KEY (random ≥32 chars), POSTGRES_PASSWORD, PRISM_ADMIN_EMAIL/PASSWORD, PRISM_DOMAIN
docker compose up -d --build
```

| Service | Role | Exposed |
|---|---|---|
| `proxy` | Caddy: serves the web app, reverse-proxies `/api`, automatic HTTPS when `PRISM_DOMAIN` is a hostname | 80/443 only |
| `api` | FastAPI (2 uvicorn workers), health check `/api/health` | internal |
| `worker` | background jobs (`python -m app.worker`); scale with `docker compose up -d --scale worker=3` | internal |
| `migrate` | one-shot `alembic upgrade head` before api/worker start | — |
| `db` | PostgreSQL 16 | internal |
| `ollama` | local AI model server for Blueprint interpretation (used unless `ANTHROPIC_API_KEY` is set or `PRISM_AI_PROVIDER` says otherwise) | internal |
| `ollama-pull` | one-shot: downloads `PRISM_OLLAMA_MODEL` on first start | — |

Volumes: `prism-db` (Postgres), `prism-data` (original files, Parquet dataset versions, generated workbooks), `caddy-data` (TLS certificates), `ollama-models` (downloaded AI models).

AI model choice (`PRISM_OLLAMA_MODEL`): no GPU → `qwen2.5:7b` (works, a Blueprint takes minutes); ~16 GB GPU → a 27B-class Qwen model; 24 GB+ → 30B+. For a GPU, uncomment the `deploy` block of the `ollama` service. To use Claude or DeepSeek instead, set the variables in `.env` (the `ollama` services can then be removed). The web app shows the active model and warns when it is unreachable or not downloaded.

Upgrading: `git pull && docker compose up -d --build` (migrations run automatically).

Security checklist: strong `PRISM_SECRET_KEY`; remove `PRISM_ADMIN_PASSWORD` from `.env` after the first login; firewall the server to 80/443; back up regularly ([BACKUP.md](BACKUP.md)).

> **Status:** these files were written and reviewed but **not yet executed** on a Docker host (Docker was unavailable on the development machine). Do the first deployment on a staging server.
