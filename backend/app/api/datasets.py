from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import duckdb
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import WorkspaceCtx, admin, viewer
from app.core.errors import NotFound
from app.db.models import AuditEvent, Dataset, DatasetVersion, Source
from app.services.storage import get_storage

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["datasets"])


class VersionOut(BaseModel):
    id: int
    version: int
    row_count: int
    content_hash: str
    created_at: datetime
    source_id: int | None


class DatasetOut(BaseModel):
    id: int
    name: str
    sheet: str | None
    source_id: int | None
    source_filename: str | None
    current_version: int
    created_at: datetime
    version: VersionOut
    columns: list[dict[str, Any]]
    profile: dict[str, Any]
    lineage: dict[str, Any]


class RowsOut(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    total: int
    offset: int
    limit: int
    layer: str


class AuditOut(BaseModel):
    id: int
    at: datetime
    actor_id: int | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]


def _dataset(ctx: WorkspaceCtx, dataset_id: int) -> Dataset:
    ds = ctx.db.get(Dataset, dataset_id)
    if ds is None or ds.workspace_id != ctx.workspace.id:
        raise NotFound("Dataset not found.")
    return ds


def _version(ctx: WorkspaceCtx, ds: Dataset, version: int | None) -> DatasetVersion:
    v = ctx.db.execute(
        select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id, DatasetVersion.version == (version or ds.current_version))
    ).scalar_one_or_none()
    if v is None:
        raise NotFound("Version not found.")
    return v


def _vout(v: DatasetVersion) -> VersionOut:
    return VersionOut(id=v.id, version=v.version, row_count=v.row_count, content_hash=v.content_hash, created_at=v.created_at, source_id=v.source_id)


@router.get("/datasets", response_model=list[dict])
def list_datasets(ctx: WorkspaceCtx = Depends(viewer)) -> list[dict]:
    rows = ctx.db.execute(
        select(Dataset, DatasetVersion)
        .join(DatasetVersion, (DatasetVersion.dataset_id == Dataset.id) & (DatasetVersion.version == Dataset.current_version))
        .where(Dataset.workspace_id == ctx.workspace.id).order_by(Dataset.id.desc()).limit(1000)
    ).all()
    return [{
        "id": d.id, "name": d.name, "sheet": d.sheet, "source_id": d.source_id, "current_version": d.current_version,
        "row_count": v.row_count, "column_count": len(v.columns), "quality_score": v.profile.get("quality_score"),
        "created_at": d.created_at.isoformat(),
    } for d, v in rows]


@router.get("/datasets/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset_id: int, version: int | None = Query(None, ge=1), ctx: WorkspaceCtx = Depends(viewer)) -> DatasetOut:
    ds = _dataset(ctx, dataset_id)
    v = _version(ctx, ds, version)
    src = ctx.db.get(Source, ds.source_id) if ds.source_id else None
    profile = dict(v.profile)
    profile["columns"] = [{k: val for k, val in c.items() if k != "failure_examples_all"} for c in profile.get("columns", [])]
    return DatasetOut(
        id=ds.id, name=ds.name, sheet=ds.sheet, source_id=ds.source_id, source_filename=src.filename if src else None,
        current_version=ds.current_version, created_at=ds.created_at, version=_vout(v), columns=v.columns,
        profile=profile, lineage=v.lineage,
    )


@router.get("/datasets/{dataset_id}/versions", response_model=list[VersionOut])
def list_versions(dataset_id: int, ctx: WorkspaceCtx = Depends(viewer)) -> list[VersionOut]:
    ds = _dataset(ctx, dataset_id)
    vs = ctx.db.execute(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id).order_by(DatasetVersion.version.desc())).scalars().all()
    return [_vout(v) for v in vs]


@router.get("/datasets/{dataset_id}/rows", response_model=RowsOut)
def get_rows(
    dataset_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    layer: str = Query("clean", pattern="^(clean|raw)$"),
    version: int | None = Query(None, ge=1),
    ctx: WorkspaceCtx = Depends(viewer),
) -> RowsOut:
    ds = _dataset(ctx, dataset_id)
    v = _version(ctx, ds, version)
    path = get_storage().resolve(v.clean_path if layer == "clean" else v.raw_path)
    con = duckdb.connect()
    try:
        rel = con.execute("SELECT * FROM read_parquet(?) ORDER BY _prism_row LIMIT ? OFFSET ?", [str(path), limit, offset])
        df = rel.df()
    finally:
        con.close()
    payload = json.loads(df.to_json(orient="split", date_format="iso", index=False))
    return RowsOut(columns=payload["columns"], rows=payload["data"], total=v.row_count, offset=offset, limit=limit, layer=layer)


@router.get("/audit", response_model=list[AuditOut])
def list_audit(limit: int = Query(200, ge=1, le=1000), ctx: WorkspaceCtx = Depends(admin)) -> list[AuditOut]:
    evs = ctx.db.execute(
        select(AuditEvent).where(AuditEvent.workspace_id == ctx.workspace.id).order_by(AuditEvent.id.desc()).limit(limit)
    ).scalars().all()
    return [AuditOut(id=e.id, at=e.at, actor_id=e.actor_id, action=e.action, target_type=e.target_type, target_id=e.target_id, details=e.details) for e in evs]
