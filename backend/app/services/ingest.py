"""Source ingestion and workbook generation jobs."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.vision import make_ocr
from app.core.config import get_settings
from app.core.errors import IngestionError, NotFound
from app.db.models import Artifact, Dataset, DatasetVersion, Job, Source
from app.excel.builder import WorkbookDataset, WorkbookSource, build_workbook
from app.ingestion.pipeline import ingest
from app.ingestion.readers import SUPPORTED_KINDS, sniff_kind
from app.profiling.profiler import profile_table
from app.services.audit import audit
from app.services.jobs import enqueue, job_handler
from app.services.storage import get_storage

ALLOWED_EXT = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".tab", ".txt", ".json", ".jsonl", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


def safe_filename(name: str | None, default: str = "data") -> str:
    name = (name or default).replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f<>:\"|?*]", "_", name).strip(" .") or default
    return name[:200]


def create_source(db: Session, *, workspace_id: int, user_id: int | None, data: bytes, filename: str | None,
                  content_type: str | None = None, ip: str | None = None) -> tuple[Source, Job]:
    s = get_settings()
    if not data:
        raise IngestionError("The upload is empty.")
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise IngestionError(f"File exceeds the {s.max_upload_mb} MB limit.")
    filename = safe_filename(filename)
    ext = filename[filename.rfind("."):].lower() if "." in filename else ""
    if ext and ext not in ALLOWED_EXT:
        raise IngestionError(f"Unsupported file type '{ext}'.")
    kind = sniff_kind(data, filename)  # validates magic bytes vs. extension
    if kind not in SUPPORTED_KINDS:
        raise IngestionError("Unsupported file type.")
    storage = get_storage()
    rel, sha = storage.save_source(workspace_id, data, kind)
    src = Source(
        workspace_id=workspace_id, kind=kind, filename=filename, content_type=content_type, sha256=sha,
        size_bytes=len(data), storage_path=rel, status="pending", created_by=user_id,
    )
    db.add(src)
    db.flush()
    audit(db, "source.upload", workspace_id=workspace_id, actor_id=user_id, target_type="source", target_id=src.id,
          details={"filename": filename, "kind": kind, "size": len(data), "sha256": sha}, ip=ip)
    db.commit()
    job = enqueue(db, "ingest_source", {"source_id": src.id}, workspace_id=workspace_id, user_id=user_id)
    db.refresh(src)
    return src, job


@job_handler("ingest_source")
def ingest_source_job(db: Session, job: Job) -> dict[str, Any]:
    src = db.get(Source, job.payload["source_id"])
    if src is None:
        raise NotFound("Source not found.")
    src.status = "processing"
    db.commit()
    storage = get_storage()
    data = storage.read_bytes(src.storage_path)
    try:
        result = ingest(data, src.filename, kind=src.kind, ocr=make_ocr())
    except IngestionError as e:
        src.status, src.error = "failed", e.message
        db.commit()
        raise
    if not result.tables:
        src.status, src.error = "failed", "No tables with data were found in this source."
        src.summary = {"sheets": result.sheets, "notes": result.notes}
        db.commit()
        raise IngestionError(src.error)

    dataset_ids = []
    for idx, t in enumerate(result.tables):
        ds = Dataset(workspace_id=src.workspace_id, source_id=src.id, name=t.name, sheet=t.sheet, table_index=idx)
        db.add(ds)
        db.flush()
        create_version(db, ds, t, src.id)
        dataset_ids.append(ds.id)
    src.status = "ready"
    src.error = None
    src.summary = {"sheets": result.sheets, "notes": result.notes, "tables": len(result.tables)}
    audit(db, "source.ingested", workspace_id=src.workspace_id, actor_id=job.created_by, target_type="source",
          target_id=src.id, details={"datasets": dataset_ids})
    db.commit()
    # generate the workbook right away so the user has a deliverable
    art = generate_workbook(db, src, user_id=job.created_by)
    return {"source_id": src.id, "dataset_ids": dataset_ids, "artifact_id": art.id}


def create_version(db: Session, ds: Dataset, t, source_id: int | None) -> DatasetVersion:
    storage = get_storage()
    version = ds.current_version + 1
    vdir = storage.version_dir(ds.workspace_id, ds.id, version)
    clean_rel = storage.write_parquet(t.clean, f"{vdir}/clean.parquet")
    raw_rel = storage.write_parquet(t.raw, f"{vdir}/raw.parquet")
    profile = profile_table(t)
    h = hashlib.sha256(storage.resolve(clean_rel).read_bytes()).hexdigest()
    # keep full failure lists in the profile for the workbook's PRISM flags (bounded)
    for c in profile["columns"]:
        fails = t.failures.get(c["key"], [])
        c["failure_examples_all"] = [{"row": r, "value": v} for r, v in fails[:5000]]
    v = DatasetVersion(
        dataset_id=ds.id, version=version, source_id=source_id, row_count=len(t.clean), columns=t.columns,
        profile=profile, lineage=t.lineage, clean_path=clean_rel, raw_path=raw_rel, content_hash=h,
    )
    db.add(v)
    ds.current_version = version
    db.flush()
    return v


def current_version(db: Session, ds: Dataset) -> DatasetVersion:
    v = db.execute(
        select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id, DatasetVersion.version == ds.current_version)
    ).scalar_one_or_none()
    if v is None:
        raise NotFound("Dataset has no versions.")
    return v


def generate_workbook(db: Session, src: Source, user_id: int | None) -> Artifact:
    storage = get_storage()
    datasets = db.execute(select(Dataset).where(Dataset.source_id == src.id).order_by(Dataset.table_index)).scalars().all()
    if not datasets:
        raise NotFound("Source has no datasets.")
    from app.blueprint.context import DatasetContext
    from app.blueprint.kpi import evaluate_kpis
    from app.blueprint.service import latest_applied

    bp = latest_applied(db, src.id)
    plans = {p.dataset_id: p for p in bp.datasets} if bp else {}
    wds = []
    version_ids = []
    for ds in datasets:
        v = current_version(db, ds)
        version_ids.append(v.id)
        df = storage.read_parquet(v.clean_path)
        kpis: list[dict] = []
        if ds.id in plans:
            # KPI values are recomputed on exactly the data written to this workbook
            approved = [k for k in plans[ds.id].kpis if k.status == "approved"]
            ctx = DatasetContext(dataset_id=ds.id, version=v.version, version_id=v.id, name=ds.name, columns=v.columns,
                                 profile=v.profile, lineage=v.lineage, df=df)
            evaluate_kpis(approved, ctx)
            kpis = [k.model_dump(mode="json") for k in approved if k.status == "approved"]
        wds.append(WorkbookDataset(name=ds.name, columns=v.columns, df=df, profile=v.profile, lineage=v.lineage, kpis=kpis))
    original = storage.read_bytes(src.storage_path) if src.kind == "xlsx" else None
    data = build_workbook(wds, WorkbookSource(src.filename, src.kind, src.sha256, src.size_bytes, original),
                          blueprint=bp.model_dump(mode="json") if bp else None)
    stamp = datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y%m%d-%H%M")
    base = src.filename.rsplit(".", 1)[0] if "." in src.filename else src.filename
    fname = safe_filename(f"PRISM_{base}_{stamp}.xlsx")
    rel, sha = storage.save_artifact(src.workspace_id, data, re.sub(r"[^\w.\-]", "_", fname, flags=re.ASCII))
    art = Artifact(
        workspace_id=src.workspace_id, source_id=src.id, kind="workbook", filename=fname, storage_path=rel,
        size_bytes=len(data), sha256=sha, meta={"dataset_version_ids": version_ids}, created_by=user_id,
    )
    db.add(art)
    db.flush()
    audit(db, "workbook.generated", workspace_id=src.workspace_id, actor_id=user_id, target_type="artifact",
          target_id=art.id, details={"source_id": src.id, "size": len(data)})
    db.commit()
    return art


@job_handler("build_workbook")
def build_workbook_job(db: Session, job: Job) -> dict[str, Any]:
    src = db.get(Source, job.payload["source_id"])
    if src is None:
        raise NotFound("Source not found.")
    art = generate_workbook(db, src, user_id=job.created_by)
    return {"artifact_id": art.id}
