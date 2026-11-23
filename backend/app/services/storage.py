"""File storage under PRISM_DATA_DIR. Paths stored in the DB are relative to the root;
``resolve`` refuses anything that escapes it (path traversal protection)."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pandas as pd

from app.core.config import get_settings


class Storage:
    def __init__(self, root: Path | None = None):
        self.root = (root or get_settings().data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if self.root != p and self.root not in p.parents:
            raise ValueError("Path escapes storage root.")
        return p

    def _atomic_write(self, rel: str, data: bytes) -> str:
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return rel

    def save_source(self, workspace_id: int, data: bytes, kind: str) -> tuple[str, str]:
        sha = hashlib.sha256(data).hexdigest()
        rel = f"ws_{workspace_id}/sources/{sha}.{kind}"
        if not self.resolve(rel).exists():
            self._atomic_write(rel, data)
        return rel, sha

    def read_bytes(self, rel: str) -> bytes:
        return self.resolve(rel).read_bytes()

    def version_dir(self, workspace_id: int, dataset_id: int, version: int) -> str:
        return f"ws_{workspace_id}/datasets/{dataset_id}/v{version}"

    def write_parquet(self, df: pd.DataFrame, rel: str) -> str:
        p = self.resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        df.to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, p)
        return rel

    def read_parquet(self, rel: str) -> pd.DataFrame:
        return pd.read_parquet(self.resolve(rel))

    def save_artifact(self, workspace_id: int, data: bytes, name: str) -> tuple[str, str]:
        sha = hashlib.sha256(data).hexdigest()
        rel = f"ws_{workspace_id}/artifacts/{sha[:16]}_{name}"
        self._atomic_write(rel, data)
        return rel, sha


def get_storage() -> Storage:
    return Storage()
