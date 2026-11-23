FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_NO_CACHE_DIR=1 TZ=Asia/Vientiane
RUN apt-get update && apt-get install -y --no-install-recommends tzdata curl && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 prism
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .  # installs dependencies declared in pyproject
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN mkdir -p /data && chown -R prism:prism /data /app
USER prism
ENV PRISM_DATA_DIR=/data PRISM_ENV=production PRISM_JOBS_MODE=external
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
