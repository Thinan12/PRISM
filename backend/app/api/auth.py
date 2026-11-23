from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, current_user
from app.core.config import get_settings
from app.core.errors import PrismError, Unauthorized
from app.core.security import create_token, rate_limiter, verify_password
from app.db.models import Membership, User, Workspace
from app.db.session import get_db
from app.services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class TooManyRequests(PrismError):
    status_code = 429
    code = "rate_limited"


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class WorkspaceRef(BaseModel):
    id: int
    name: str
    slug: str
    role: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    is_superadmin: bool
    workspaces: list[WorkspaceRef]


class LoginOut(BaseModel):
    token: str
    user: UserOut


def user_out(db: Session, user: User) -> UserOut:
    if user.is_superadmin:
        wss = [WorkspaceRef(id=w.id, name=w.name, slug=w.slug, role="admin") for w in db.execute(select(Workspace).order_by(Workspace.id)).scalars()]
    else:
        rows = db.execute(
            select(Workspace, Membership.role).join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id).order_by(Workspace.id)
        ).all()
        wss = [WorkspaceRef(id=w.id, name=w.name, slug=w.slug, role=r) for w, r in rows]
    return UserOut(id=user.id, email=user.email, name=user.name, is_superadmin=user.is_superadmin, workspaces=wss)


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> LoginOut:
    ip = client_ip(request)
    if not rate_limiter.check(f"login:{ip}", get_settings().auth_rate_per_minute):
        raise TooManyRequests("Too many login attempts. Try again in a minute.")
    user = db.execute(select(User).where(User.email == body.email.strip().lower())).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        audit(db, "auth.login_failed", details={"email": body.email.strip().lower()}, ip=ip)
        db.commit()
        raise Unauthorized("Incorrect email or password.")
    audit(db, "auth.login", actor_id=user.id, ip=ip)
    db.commit()
    return LoginOut(token=create_token(user.id, user.token_version), user=user_out(db, user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserOut:
    return user_out(db, user)


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    """Invalidate every token issued to the current user."""
    u = db.get(User, user.id)
    u.token_version += 1
    audit(db, "auth.logout_all", actor_id=u.id, ip=client_ip(request))
    db.commit()
