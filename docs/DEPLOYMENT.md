# Deployment (company Linux server)

Requirements: Docker Engine ≥ 24 with the Compose plugin, ~2 GB RAM, disk for uploads.

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

Volumes: `prism-db` (Postgres), `prism-data` (original files, Parquet dataset versions, generated workbooks), `caddy-data` (TLS certificates).

Upgrading: `git pull && docker compose up -d --build` (migrations run automatically).

Security checklist: strong `PRISM_SECRET_KEY`; remove `PRISM_ADMIN_PASSWORD` from `.env` after the first login; firewall the server to 80/443; back up regularly ([BACKUP.md](BACKUP.md)).

> **Status:** these files were written and reviewed but **not yet executed** on a Docker host (Docker was unavailable on the development machine). Do the first deployment on a staging server.
