"""Analytics lifecycle: question jobs, dashboard generation / editing, shared helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.dashboard import DashboardContent, generate_content, validate_content
from app.analytics.engine import QueryInvalid, dataset_context, run_query
from app.analytics.nl import compose_answer, interpret_question
from app.blueprint.context import DatasetContext
from app.blueprint.service import latest_applied
from app.core.errors import Conflict, NotFound, PrismError
from app.core.logging import log
from app.db.models import DashboardRecord, Dataset, Job, QuestionRecord, Source
from app.services.audit import audit
from app.services.jobs import job_handler

MAX_RESULT_ROWS_STORED = 1000


class InvalidQuery(PrismError):
    code = "invalid_query"
    status_code = 422


def source_contexts(db: Session, source_id: int) -> dict[int, DatasetContext]:
    dss = db.execute(select(Dataset).where(Dataset.source_id == source_id).order_by(Dataset.table_index)).scalars().all()
    return {ds.id: dataset_context(db, ds) for ds in dss}


# ---------------------------------------------------------------------------
# questions
# ---------------------------------------------------------------------------


@job_handler("answer_question")
def answer_question_job(db: Session, job: Job) -> dict[str, Any]:
    rec = db.get(QuestionRecord, job.payload["question_id"])
    if rec is None:
        raise NotFound("Question not found.")
    ds = db.get(Dataset, rec.dataset_id)
    if ds is None:
        raise NotFound("Dataset not found.")
    try:
        ctx = dataset_context(db, ds, rec.dataset_version)
        parsed = interpret_question(rec.question, ctx, job.payload.get("engine", "auto"))
        rec.engine, rec.model, rec.notes, rec.fallback_reason = parsed.engine, parsed.model, parsed.notes, parsed.fallback_reason
        if parsed.spec is None:
            rec.status, rec.answer = "unanswerable", parsed.reason
        else:
            res = run_query(parsed.spec, ctx)
            rec.spec = res.spec.model_dump(mode="json")
            rec.chart = parsed.chart.value
            rec.answer = compose_answer(res)
            stored = res.model_dump(mode="json")
            stored["rows"] = stored["rows"][:MAX_RESULT_ROWS_STORED]
            rec.result = stored
            rec.status = "answered"
    except QueryInvalid as e:
        rec.status, rec.error = "failed", str(e)
    except Exception as e:
        rec.status, rec.error = "failed", f"{type(e).__name__}: {str(e)[:300]}"
        log.error("question_failed", question_id=rec.id, error=repr(e))
    audit(db, "question.answered", workspace_id=rec.workspace_id, actor_id=rec.user_id, target_type="question", target_id=rec.id,
          details={"status": rec.status, "engine": rec.engine, "dataset_id": rec.dataset_id, "sql": (rec.result or {}).get("sql")})
    db.commit()
    return {"question_id": rec.id, "status": rec.status}


# ---------------------------------------------------------------------------
# dashboards
# ---------------------------------------------------------------------------


def create_dashboard(db: Session, src: Source, user_id: int | None, name: str | None = None) -> tuple[DashboardRecord, list[str]]:
    contexts = source_contexts(db, src.id)
    bp = latest_applied(db, src.id)
    content, origin, notes = generate_content(list(contexts.values()), bp, src.id)
    rec = DashboardRecord(workspace_id=src.workspace_id, source_id=src.id, name=(name or f"{src.filename} — overview")[:200],
                          origin=origin, content=content.model_dump(mode="json"), revision=1, created_by=user_id, updated_by=user_id)
    db.add(rec)
    db.flush()
    audit(db, "dashboard.created", workspace_id=src.workspace_id, actor_id=user_id, target_type="dashboard", target_id=rec.id,
          details={"source_id": src.id, "origin": origin, "panels": len(content.panels), "notes": notes[:10]})
    return rec, notes


def update_dashboard(db: Session, rec: DashboardRecord, content: DashboardContent, name: str | None, revision: int,
                     user_id: int | None) -> DashboardRecord:
    if revision != rec.revision:
        raise Conflict("This dashboard was changed by someone else. Reload it and apply your edit again.")
    try:
        content = validate_content(content, source_contexts(db, rec.source_id))
    except QueryInvalid as e:
        raise InvalidQuery(str(e)) from e
    rec.content = content.model_dump(mode="json")
    if name:
        rec.name = name[:200]
    rec.origin = "user" if rec.origin != "user" else rec.origin
    rec.revision += 1
    rec.updated_by = user_id
    audit(db, "dashboard.updated", workspace_id=rec.workspace_id, actor_id=user_id, target_type="dashboard", target_id=rec.id,
          details={"revision": rec.revision, "panels": len(content.panels)})
    return rec
