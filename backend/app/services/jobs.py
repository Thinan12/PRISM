"""DB-backed job queue (ARCHITECTURE ADR-1).

Handlers are registered with ``@job_handler("type")`` and receive (session, job). They
return a JSON-serializable result dict. Exceptions mark the job failed (with retries up
to ``max_attempts``); expected ``PrismError``s are stored as user-facing messages.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import PrismError
from app.core.logging import log
from app.db.models import Job, utcnow
from app.db.session import get_sessionmaker

Handler = Callable[[Session, Job], dict[str, Any]]
HANDLERS: dict[str, Handler] = {}
STALE_AFTER = timedelta(minutes=30)


def job_handler(name: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn

    return deco


def enqueue(db: Session, type_: str, payload: dict[str, Any], *, workspace_id: int | None, user_id: int | None, max_attempts: int = 1) -> Job:
    if type_ not in HANDLERS:
        raise ValueError(f"Unknown job type {type_}")
    job = Job(type=type_, payload=payload, workspace_id=workspace_id, created_by=user_id, max_attempts=max_attempts)
    db.add(job)
    db.commit()
    if get_settings().jobs_mode == "inline":
        run_job(job.id)
        db.refresh(job)
    else:
        _wake.set()
    return job


def claim_next(db: Session) -> Job | None:
    """Atomically claim the oldest queued job (SKIP LOCKED on Postgres, guarded UPDATE elsewhere)."""
    s = get_settings()
    q = select(Job).where(Job.status == "queued").order_by(Job.created_at, Job.id).limit(1)
    if not s.is_sqlite:
        q = q.with_for_update(skip_locked=True)
    job = db.execute(q).scalar_one_or_none()
    if job is None:
        return None
    res = db.execute(
        update(Job).where(Job.id == job.id, Job.status == "queued")
        .values(status="running", started_at=utcnow(), attempts=Job.attempts + 1)
    )
    db.commit()
    if res.rowcount != 1:
        return None
    db.refresh(job)
    return job


def run_job(job_id: int) -> None:
    Session_ = get_sessionmaker()
    with Session_() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        if job.status == "queued":
            job.status = "running"
            job.started_at = utcnow()
            job.attempts += 1
            db.commit()
        handler = HANDLERS.get(job.type)
        try:
            if handler is None:
                raise PrismError(f"No handler for job type {job.type}")
            result = handler(db, job)
            job.status = "succeeded"
            job.result = result
            job.progress = 1.0
            job.error = None
        except PrismError as e:
            db.rollback()
            job = db.get(Job, job_id)
            job.status = "failed"
            job.error = e.message
            log.warning("job_failed", job_id=job_id, type=job.type, error=e.message)
        except Exception as e:  # unexpected: retry if allowed
            db.rollback()
            job = db.get(Job, job_id)
            log.error("job_crashed", job_id=job_id, type=job.type, error=str(e), tb=traceback.format_exc())
            if job.attempts < job.max_attempts:
                job.status = "queued"
            else:
                job.status = "failed"
                job.error = f"Internal error while processing: {type(e).__name__}"
        job.finished_at = utcnow() if job.status in ("succeeded", "failed") else None
        db.commit()
        _after_hooks(db, job)


AFTER_HOOKS: list[Callable[[Session, Job], None]] = []


def _after_hooks(db: Session, job: Job) -> None:
    for h in AFTER_HOOKS:
        try:
            h(db, job)
        except Exception:  # hooks must never break the worker
            log.error("job_hook_failed", job_id=job.id, tb=traceback.format_exc())


def requeue_stale(db: Session) -> int:
    cutoff = utcnow() - STALE_AFTER
    res = db.execute(
        update(Job).where(Job.status == "running", Job.started_at < cutoff).values(status="queued")
    )
    db.commit()
    return res.rowcount or 0


_wake = threading.Event()


def worker_loop(stop: threading.Event) -> None:
    s = get_settings()
    Session_ = get_sessionmaker()
    with Session_() as db:
        n = requeue_stale(db)
        if n:
            log.warning("requeued_stale_jobs", count=n)
    log.info("worker_started", mode=s.jobs_mode)
    while not stop.is_set():
        try:
            with Session_() as db:
                job = claim_next(db)
            if job is not None:
                run_job(job.id)
                continue
        except Exception:
            log.error("worker_loop_error", tb=traceback.format_exc())
        _wake.wait(s.worker_poll_seconds)
        _wake.clear()
    log.info("worker_stopped")
