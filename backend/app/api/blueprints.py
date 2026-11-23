from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import WorkspaceCtx, client_ip, editor, viewer
from app.api.sources import JobOut, _get_source, job_out
from app.blueprint.ai import ai_configured
from app.blueprint.service import Edit, edit_blueprint, get_record
from app.core.config import get_settings
from app.core.errors import Conflict, IngestionError
from app.db.models import BlueprintRecord
from app.services.audit import audit
from app.services.jobs import enqueue

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["blueprints"])


class BlueprintOut(BaseModel):
    id: int
    source_id: int
    number: int
    status: str
    engine: str
    requirements: str | None
    created_at: datetime
    updated_at: datetime | None
    applied_at: datetime | None
    result: dict[str, Any] | None
    content: dict[str, Any]


class GenerateIn(BaseModel):
    requirements: str | None = Field(default=None, max_length=2000)


class EditsIn(BaseModel):
    edits: list[Edit] = Field(min_length=1, max_length=200)


class AIStatus(BaseModel):
    configured: bool
    model: str


def out(rec: BlueprintRecord) -> BlueprintOut:
    return BlueprintOut(id=rec.id, source_id=rec.source_id, number=rec.number, status=rec.status, engine=rec.engine,
                        requirements=rec.requirements, created_at=rec.created_at, updated_at=rec.updated_at,
                        applied_at=rec.applied_at, result=rec.result, content=rec.content)


@router.get("/ai-status", response_model=AIStatus, summary="Whether AI interpretation is configured")
def ai_status(ctx: WorkspaceCtx = Depends(viewer)) -> AIStatus:
    return AIStatus(configured=ai_configured(), model=get_settings().ai_model)


@router.post("/sources/{source_id}/blueprints", response_model=JobOut, status_code=202, summary="Generate a Blueprint (AI with rule-based fallback)")
def generate(source_id: int, body: GenerateIn, request: Request, ctx: WorkspaceCtx = Depends(editor)) -> JobOut:
    src = _get_source(ctx, source_id)
    if src.status != "ready":
        raise IngestionError("Source is not ready.")
    job = enqueue(ctx.db, "generate_blueprint", {"source_id": src.id, "requirements": body.requirements},
                  workspace_id=ctx.workspace.id, user_id=ctx.user.id)
    return job_out(job)


@router.get("/sources/{source_id}/blueprints", response_model=list[BlueprintOut])
def list_for_source(source_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> list[BlueprintOut]:
    src = _get_source(ctx, source_id)
    recs = ctx.db.execute(select(BlueprintRecord).where(BlueprintRecord.source_id == src.id).order_by(BlueprintRecord.number.desc()).limit(50)).scalars().all()
    return [out(r) for r in recs]


@router.get("/sources/{source_id}/blueprint", response_model=BlueprintOut | None, summary="Latest blueprint for a source (null if none)")
def latest(source_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> BlueprintOut | None:
    src = _get_source(ctx, source_id)
    rec = ctx.db.execute(select(BlueprintRecord).where(BlueprintRecord.source_id == src.id, BlueprintRecord.status != "superseded")
                         .order_by(BlueprintRecord.number.desc())).scalars().first()
    return out(rec) if rec else None


@router.get("/blueprints/{blueprint_id}", response_model=BlueprintOut)
def get(blueprint_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> BlueprintOut:
    return out(get_record(ctx.db, ctx.workspace.id, blueprint_id))


@router.patch("/blueprints/{blueprint_id}", response_model=BlueprintOut, summary="Approve, reject, rename, remove or modify blueprint items")
def patch(blueprint_id: int, body: EditsIn, ctx: WorkspaceCtx = Depends(editor)) -> BlueprintOut:
    rec = get_record(ctx.db, ctx.workspace.id, blueprint_id)
    return out(edit_blueprint(ctx.db, rec, body.edits, ctx.user.id))


@router.post("/blueprints/{blueprint_id}/apply", response_model=JobOut, status_code=202, summary="Execute approved operations → new dataset versions + workbook")
def apply(blueprint_id: int, request: Request, ctx: WorkspaceCtx = Depends(editor)) -> JobOut:
    rec = get_record(ctx.db, ctx.workspace.id, blueprint_id)
    if rec.status != "draft":
        raise Conflict(f"Blueprint is {rec.status}.")
    audit(ctx.db, "blueprint.apply_requested", workspace_id=ctx.workspace.id, actor_id=ctx.user.id, target_type="blueprint",
          target_id=rec.id, ip=client_ip(request))
    ctx.db.commit()
    job = enqueue(ctx.db, "apply_blueprint", {"blueprint_id": rec.id}, workspace_id=ctx.workspace.id, user_id=ctx.user.id)
    return job_out(job)
