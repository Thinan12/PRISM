from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import WorkspaceCtx, client_ip, editor, viewer
from app.core.config import get_settings
from app.core.errors import IngestionError, NotFound
from app.db.models import Artifact, Dataset, DatasetVersion, Job, Source
from app.services.audit import audit
from app.services.ingest import create_source
from app.services.jobs import enqueue
from app.services.storage import get_storage

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["sources"])


class JobOut(BaseModel):
    id: int
    type: str
    status: str
    progress: float
    error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


class DatasetBrief(BaseModel):
    id: int
    name: str
    sheet: str | None
    current_version: int
    row_count: int | None = None
    column_count: int | None = None
    quality_score: float | None = None


class ArtifactOut(BaseModel):
    id: int
    kind: str
    filename: str
    size_bytes: int
    created_at: datetime


class SourceOut(BaseModel):
    id: int
    kind: str
    filename: str
    size_bytes: int
    sha256: str
    status: str
    error: str | None
    summary: dict[str, Any] | None
    created_at: datetime
    datasets: list[DatasetBrief] = []
    artifacts: list[ArtifactOut] = []
    job: JobOut | None = None


class PasteIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000_000)
    name: str = Field(default="Pasted data", max_length=200)


def job_out(j: Job) -> JobOut:
    return JobOut(id=j.id, type=j.type, status=j.status, progress=j.progress, error=j.error, result=j.result,
                  created_at=j.created_at, finished_at=j.finished_at)


def source_out(ctx: WorkspaceCtx, src: Source, job: Job | None = None) -> SourceOut:
    db = ctx.db
    dss = db.execute(select(Dataset).where(Dataset.source_id == src.id).order_by(Dataset.table_index)).scalars().all()
    briefs = []
    for d in dss:
        v = db.execute(select(DatasetVersion).where(DatasetVersion.dataset_id == d.id, DatasetVersion.version == d.current_version)).scalar_one_or_none()
        briefs.append(DatasetBrief(
            id=d.id, name=d.name, sheet=d.sheet, current_version=d.current_version,
            row_count=v.row_count if v else None, column_count=len(v.columns) if v else None,
            quality_score=v.profile.get("quality_score") if v else None,
        ))
    arts = db.execute(select(Artifact).where(Artifact.source_id == src.id).order_by(Artifact.id.desc())).scalars().all()
    return SourceOut(
        id=src.id, kind=src.kind, filename=src.filename, size_bytes=src.size_bytes, sha256=src.sha256,
        status=src.status, error=src.error, summary=src.summary, created_at=src.created_at, datasets=briefs,
        artifacts=[ArtifactOut(id=a.id, kind=a.kind, filename=a.filename, size_bytes=a.size_bytes, created_at=a.created_at) for a in arts],
        job=job_out(job) if job else None,
    )


def _get_source(ctx: WorkspaceCtx, source_id: int) -> Source:
    src = ctx.db.get(Source, source_id)
    if src is None or src.workspace_id != ctx.workspace.id:
        raise NotFound("Source not found.")
    return src


@router.post("/sources", response_model=SourceOut, status_code=201, summary="Upload a file for ingestion")
async def upload_source(request: Request, file: UploadFile = File(...), ctx: WorkspaceCtx = Depends(editor)) -> SourceOut:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise IngestionError(f"File exceeds the {get_settings().max_upload_mb} MB limit.")
    src, job = create_source(ctx.db, workspace_id=ctx.workspace.id, user_id=ctx.user.id, data=data,
                             filename=file.filename, content_type=file.content_type, ip=client_ip(request))
    return source_out(ctx, src, job)


@router.post("/sources/paste", response_model=SourceOut, status_code=201, summary="Ingest pasted text (CSV/TSV/Markdown/aligned)")
def paste_source(body: PasteIn, request: Request, ctx: WorkspaceCtx = Depends(editor)) -> SourceOut:
    name = body.name.strip() or "Pasted data"
    src, job = create_source(ctx.db, workspace_id=ctx.workspace.id, user_id=ctx.user.id,
                             data=body.text.encode("utf-8"), filename=f"{name}.txt", content_type="text/plain",
                             ip=client_ip(request))
    return source_out(ctx, src, job)


@router.get("/sources", response_model=list[SourceOut])
def list_sources(ctx: WorkspaceCtx = Depends(viewer)) -> list[SourceOut]:
    srcs = ctx.db.execute(select(Source).where(Source.workspace_id == ctx.workspace.id).order_by(Source.id.desc()).limit(500)).scalars().all()
    return [source_out(ctx, s) for s in srcs]


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(source_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> SourceOut:
    return source_out(ctx, _get_source(ctx, source_id))


@router.post("/sources/{source_id}/workbook", response_model=JobOut, status_code=202, summary="Regenerate the Excel workbook")
def regenerate_workbook(source_id: int, ctx: WorkspaceCtx = Depends(editor)) -> JobOut:
    src = _get_source(ctx, source_id)
    if src.status != "ready":
        raise IngestionError("Source is not ready.")
    job = enqueue(ctx.db, "build_workbook", {"source_id": src.id}, workspace_id=ctx.workspace.id, user_id=ctx.user.id)
    return job_out(job)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> JobOut:
    j = ctx.db.get(Job, job_id)
    if j is None or j.workspace_id != ctx.workspace.id:
        raise NotFound("Job not found.")
    return job_out(j)


@router.get("/artifacts/{artifact_id}/download", summary="Download a generated file")
def download_artifact(artifact_id: int, request: Request, ctx: WorkspaceCtx = Depends(viewer)) -> FileResponse:
    a = ctx.db.get(Artifact, artifact_id)
    if a is None or a.workspace_id != ctx.workspace.id:
        raise NotFound("File not found.")
    path = get_storage().resolve(a.storage_path)
    if not path.exists():
        raise NotFound("File is missing from storage.")
    audit(ctx.db, "artifact.download", workspace_id=ctx.workspace.id, actor_id=ctx.user.id, target_type="artifact",
          target_id=a.id, ip=client_ip(request))
    ctx.db.commit()
    return FileResponse(
        path, filename=a.filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
