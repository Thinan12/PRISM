from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound, Unauthorized
from app.core.security import decode_token
from app.db.models import Membership, User, Workspace
from app.db.session import get_db

ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Authentication required.")
    payload = decode_token(authorization[7:].strip())
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active or payload.get("tv", 0) != user.token_version:
        raise Unauthorized("Session is no longer valid.")
    return user


@dataclass
class WorkspaceCtx:
    user: User
    workspace: Workspace
    role: str
    db: Session

    def can(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


def _ctx(workspace_id: int, user: User, db: Session) -> WorkspaceCtx:
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise NotFound("Workspace not found.")
    if user.is_superadmin:
        return WorkspaceCtx(user, ws, "admin", db)
    m = db.execute(
        select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == workspace_id)
    ).scalar_one_or_none()
    if m is None:
        # do not reveal existence of other tenants' workspaces
        raise NotFound("Workspace not found.")
    return WorkspaceCtx(user, ws, m.role, db)


def require(role: str):
    def dep(
        workspace_id: int = Path(..., ge=1),
        user: User = Depends(current_user),
        db: Session = Depends(get_db),
    ) -> WorkspaceCtx:
        ctx = _ctx(workspace_id, user, db)
        if not ctx.can(role):
            raise Forbidden(f"This action requires the {role} role.")
        return ctx

    return dep


viewer = require("viewer")
editor = require("editor")
admin = require("admin")
