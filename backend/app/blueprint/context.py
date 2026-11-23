"""Everything the blueprint engines know about a dataset version (profile + typed data)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Dataset, DatasetVersion, Source
from app.services.storage import get_storage


@dataclass
class DatasetContext:
    dataset_id: int
    version: int
    version_id: int
    name: str
    columns: list[dict[str, Any]]
    profile: dict[str, Any]
    lineage: dict[str, Any]
    df: pd.DataFrame
    raw: pd.DataFrame | None = None
    _prof: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._prof = {c["key"]: c for c in self.profile.get("columns", [])}

    def col(self, key: str) -> dict[str, Any] | None:
        return next((c for c in self.columns if c["key"] == key), None)

    def prof(self, key: str) -> dict[str, Any]:
        return self._prof.get(key, {})

    def distinct_values(self, key: str, limit: int = 100_000) -> set[str]:
        s = self.df[key].dropna()
        return set(s.astype(str).unique()[:limit])


def load_contexts(db: Session, source: Source, with_raw: bool = False) -> list[DatasetContext]:
    storage = get_storage()
    out = []
    dss = db.execute(select(Dataset).where(Dataset.source_id == source.id).order_by(Dataset.table_index)).scalars().all()
    for ds in dss:
        v = db.execute(
            select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id, DatasetVersion.version == ds.current_version)
        ).scalar_one()
        out.append(DatasetContext(
            dataset_id=ds.id, version=v.version, version_id=v.id, name=ds.name, columns=v.columns,
            profile=v.profile, lineage=v.lineage, df=storage.read_parquet(v.clean_path),
            raw=storage.read_parquet(v.raw_path) if with_raw else None,
        ))
    return out
