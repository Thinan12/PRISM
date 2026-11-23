from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import log
from app.db.models import AuditEvent


def audit(
    db: Session,
    action: str,
    *,
    workspace_id: int | None = None,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditEvent:
    ev = AuditEvent(
        action=action, workspace_id=workspace_id, actor_id=actor_id, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None, details=details or {}, ip=ip,
    )
    db.add(ev)
    log.info("audit", action=action, workspace_id=workspace_id, actor_id=actor_id, target=f"{target_type}:{target_id}")
    return ev
