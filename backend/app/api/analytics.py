"""Phase 3 API: queries, questions, insights and dashboards. All read endpoints are available to
viewers (branch managers); creating and editing dashboards needs the editor role."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.analytics.dashboard import (
    ActiveFilter,
    DashboardContent,
    PanelData,
    evaluate,
    export_xlsx,
    filter_options,
    insights,
)
from app.analytics.engine import QueryInvalid, QueryResult, dataset_context, run_query
from app.analytics.service import InvalidQuery, create_dashboard, source_contexts, update_dashboard
from app.analytics.spec import QuerySpec
from app.api.datasets import _dataset
from app.api.deps import WorkspaceCtx, client_ip, editor, viewer
from app.api.sources import JobOut, _get_source, job_out
from app.core.errors import NotFound
from app.db.models import DashboardRecord, QuestionRecord
from app.services.audit import audit
from app.services.jobs import enqueue

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["analytics"])


# ---------------------------------------------------------------------------
# ad-hoc queries and insights
# ---------------------------------------------------------------------------


@router.post("/datasets/{dataset_id}/query", response_model=QueryResult, summary="Run a declarative query (validated → SQL)")
def query(dataset_id: int, spec: QuerySpec, version: int | None = Query(None, ge=1), ctx: WorkspaceCtx = Depends(viewer)) -> QueryResult:
    ds = _dataset(ctx, dataset_id)
    if spec.dataset_id != ds.id:
        raise InvalidQuery("spec.dataset_id does not match the dataset in the path")
    try:
        return run_query(spec, dataset_context(ctx.db, ds, version))
    except QueryInvalid as e:
        raise InvalidQuery(str(e)) from e


@router.get("/datasets/{dataset_id}/insights", summary="Deterministic findings, each with its SQL")
def dataset_insights(dataset_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> list[dict[str, Any]]:
    ds = _dataset(ctx, dataset_id)
    return insights(dataset_context(ctx.db, ds))


# ---------------------------------------------------------------------------
# questions
# ---------------------------------------------------------------------------


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    engine: Literal["auto", "rules", "ai"] = "auto"


class QuestionOut(BaseModel):
    id: int
    dataset_id: int
    dataset_version: int
    question: str
    status: str
    engine: str | None
    model: str | None
    chart: str | None
    answer: str | None
    notes: list[str] | None
    fallback_reason: str | None
    error: str | None
    spec: dict[str, Any] | None
    result: dict[str, Any] | None
    created_at: datetime
    job: JobOut | None = None


def q_out(r: QuestionRecord, job: Any = None, with_result: bool = True) -> QuestionOut:
    return QuestionOut(id=r.id, dataset_id=r.dataset_id, dataset_version=r.dataset_version, question=r.question, status=r.status,
                       engine=r.engine, model=r.model, chart=r.chart, answer=r.answer, notes=r.notes,
                       fallback_reason=r.fallback_reason, error=r.error, spec=r.spec,
                       result=r.result if with_result else None, created_at=r.created_at,
                       job=job_out(job) if job is not None else None)


@router.post("/datasets/{dataset_id}/questions", response_model=QuestionOut, status_code=202, summary="Ask a question in Lao or English")
def ask(dataset_id: int, body: AskIn, request: Request, ctx: WorkspaceCtx = Depends(viewer)) -> QuestionOut:
    ds = _dataset(ctx, dataset_id)
    rec = QuestionRecord(workspace_id=ctx.workspace.id, dataset_id=ds.id, dataset_version=ds.current_version, user_id=ctx.user.id,
                         question=body.question.strip(), status="queued")
    ctx.db.add(rec)
    ctx.db.flush()
    audit(ctx.db, "question.asked", workspace_id=ctx.workspace.id, actor_id=ctx.user.id, target_type="question", target_id=rec.id,
          details={"dataset_id": ds.id, "question": rec.question[:500]}, ip=client_ip(request))
    ctx.db.commit()
    job = enqueue(ctx.db, "answer_question", {"question_id": rec.id, "engine": body.engine},
                  workspace_id=ctx.workspace.id, user_id=ctx.user.id)
    ctx.db.refresh(rec)
    return q_out(rec, job)


@router.get("/datasets/{dataset_id}/questions", response_model=list[QuestionOut], summary="Question history for a dataset")
def questions(dataset_id: int, limit: int = Query(30, ge=1, le=200), ctx: WorkspaceCtx = Depends(viewer)) -> list[QuestionOut]:
    ds = _dataset(ctx, dataset_id)
    rows = ctx.db.execute(select(QuestionRecord).where(QuestionRecord.dataset_id == ds.id, QuestionRecord.workspace_id == ctx.workspace.id)
                          .order_by(QuestionRecord.id.desc()).limit(limit)).scalars().all()
    return [q_out(r, with_result=False) for r in rows]


@router.get("/questions/{question_id}", response_model=QuestionOut, summary="One question with its verified result and SQL")
def question(question_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> QuestionOut:
    r = ctx.db.get(QuestionRecord, question_id)
    if r is None or r.workspace_id != ctx.workspace.id:
        raise NotFound("Question not found.")
    return q_out(r)


# ---------------------------------------------------------------------------
# dashboards
# ---------------------------------------------------------------------------


class DashboardOut(BaseModel):
    id: int
    source_id: int
    name: str
    origin: str
    revision: int
    content: DashboardContent
    created_at: datetime
    updated_at: datetime | None
    notes: list[str] = []


class DashboardPatch(BaseModel):
    revision: int
    name: str | None = Field(default=None, min_length=1, max_length=200)
    content: DashboardContent


class ViewIn(BaseModel):
    filters: list[ActiveFilter] = Field(default_factory=list, max_length=20)


class DashboardData(BaseModel):
    dashboard_id: int
    revision: int
    panels: list[PanelData]


def d_out(r: DashboardRecord, notes: list[str] | None = None) -> DashboardOut:
    return DashboardOut(id=r.id, source_id=r.source_id, name=r.name, origin=r.origin, revision=r.revision,
                        content=DashboardContent.model_validate(r.content), created_at=r.created_at, updated_at=r.updated_at,
                        notes=notes or [])


def _dash(ctx: WorkspaceCtx, dashboard_id: int) -> DashboardRecord:
    r = ctx.db.get(DashboardRecord, dashboard_id)
    if r is None or r.workspace_id != ctx.workspace.id:
        raise NotFound("Dashboard not found.")
    return r


@router.get("/sources/{source_id}/dashboards", response_model=list[DashboardOut], summary="Dashboards of a source")
def list_dashboards(source_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> list[DashboardOut]:
    src = _get_source(ctx, source_id)
    rows = ctx.db.execute(select(DashboardRecord).where(DashboardRecord.source_id == src.id).order_by(DashboardRecord.id)).scalars().all()
    return [d_out(r) for r in rows]


class CreateIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)


@router.post("/sources/{source_id}/dashboards", response_model=DashboardOut, status_code=201,
             summary="Generate a dashboard from the applied Blueprint (or a rule-based plan)")
def new_dashboard(source_id: int, body: CreateIn, ctx: WorkspaceCtx = Depends(editor)) -> DashboardOut:
    src = _get_source(ctx, source_id)
    if src.status != "ready":
        raise InvalidQuery("Source is not ready.")
    rec, notes = create_dashboard(ctx.db, src, ctx.user.id, body.name)
    ctx.db.commit()
    return d_out(rec, notes)


@router.get("/dashboards/{dashboard_id}", response_model=DashboardOut, summary="One dashboard")
def get_dashboard(dashboard_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> DashboardOut:
    return d_out(_dash(ctx, dashboard_id))


@router.patch("/dashboards/{dashboard_id}", response_model=DashboardOut, summary="Edit panels, filters or name (revision-checked)")
def patch_dashboard(dashboard_id: int, body: DashboardPatch, ctx: WorkspaceCtx = Depends(editor)) -> DashboardOut:
    rec = update_dashboard(ctx.db, _dash(ctx, dashboard_id), body.content, body.name, body.revision, ctx.user.id)
    ctx.db.commit()
    ctx.db.refresh(rec)
    return d_out(rec)


@router.delete("/dashboards/{dashboard_id}", status_code=204, summary="Delete a dashboard")
def delete_dashboard(dashboard_id: int, ctx: WorkspaceCtx = Depends(editor)) -> Response:
    rec = _dash(ctx, dashboard_id)
    audit(ctx.db, "dashboard.deleted", workspace_id=ctx.workspace.id, actor_id=ctx.user.id, target_type="dashboard", target_id=rec.id,
          details={"name": rec.name})
    ctx.db.delete(rec)
    ctx.db.commit()
    return Response(status_code=204)


@router.post("/dashboards/{dashboard_id}/data", response_model=DashboardData, summary="Compute every panel with the active filters")
def dashboard_data(dashboard_id: int, body: ViewIn, ctx: WorkspaceCtx = Depends(viewer)) -> DashboardData:
    rec = _dash(ctx, dashboard_id)
    content = DashboardContent.model_validate(rec.content)
    return DashboardData(dashboard_id=rec.id, revision=rec.revision,
                         panels=evaluate(content, source_contexts(ctx.db, rec.source_id), body.filters))


@router.get("/dashboards/{dashboard_id}/filter-options", summary="Values / ranges for the filter bar")
def dashboard_filter_options(dashboard_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> list[dict[str, Any]]:
    rec = _dash(ctx, dashboard_id)
    return filter_options(DashboardContent.model_validate(rec.content), source_contexts(ctx.db, rec.source_id))


@router.post("/dashboards/{dashboard_id}/export", summary="Excel export of the dashboard as viewed (data + SQL per panel)")
def export_dashboard(dashboard_id: int, body: ViewIn, request: Request, ctx: WorkspaceCtx = Depends(viewer)) -> Response:
    rec = _dash(ctx, dashboard_id)
    content = DashboardContent.model_validate(rec.content)
    contexts = source_contexts(ctx.db, rec.source_id)
    data = evaluate(content, contexts, body.filters)
    blob = export_xlsx(rec.name, content.panels, data, body.filters, contexts)
    audit(ctx.db, "dashboard.exported", workspace_id=ctx.workspace.id, actor_id=ctx.user.id, target_type="dashboard", target_id=rec.id,
          details={"filters": [f.model_dump(mode="json") for f in body.filters]}, ip=client_ip(request))
    ctx.db.commit()
    safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in rec.name)[:80].strip() or "dashboard"
    return Response(blob, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=\"{safe.encode('ascii', 'replace').decode()}.xlsx\"; "
                                                    f"filename*=UTF-8''{_quote(safe)}.xlsx"})


def _quote(s: str) -> str:
    from urllib.parse import quote

    return quote(s)
