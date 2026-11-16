"""JWT and Header-based Role-Based Access Control (RBAC) for AI-HAS."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

import jwt
from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import AuditLog, User, get_db

VALID_ROLES = {"employee", "support_agent", "admin"}

DEFAULT_PERSONAS = {
    "employee": {
        "email": "alex.chen@acme-corp.com",
        "full_name": "Alex Chen",
        "department": "Product Engineering",
    },
    "support_agent": {
        "email": "maya.patel@acme-corp.com",
        "full_name": "Maya Patel",
        "department": "IT Service Desk",
    },
    "admin": {
        "email": "jordan.taylor@acme-corp.com",
        "full_name": "Jordan Taylor",
        "department": "Enterprise IT & Security",
    },
}


class AuthenticatedUser(BaseModel):
    """Resolved identity and role context for the current request."""

    id: str
    email: str
    full_name: str
    role: str
    department: str
    auth_method: str = "header"


def create_access_token(
    email: str,
    role: str,
    full_name: str = "Enterprise User",
    department: str = "General",
    expires_minutes: int = 480,
) -> str:
    """Mint a signed HS256 JWT token for API clients."""
    if role not in VALID_ROLES:
        raise ValueError(f"Unsupported role '{role}'. Must be one of {sorted(VALID_ROLES)}.")

    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": email,
        "email": email,
        "role": role,
        "full_name": full_name,
        "department": department,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Validate and decode a signed HS256 JWT token."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired.",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        ) from exc

    role = payload.get("role", "")
    email = payload.get("email") or payload.get("sub")
    if role not in VALID_ROLES or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing valid role or email claims.",
        )
    return payload


def _upsert_user(
    db: Session,
    email: str,
    full_name: str,
    role: str,
    department: str,
) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user:
        updated = False
        if user.role != role:
            user.role = role
            updated = True
        if full_name and user.full_name != full_name:
            user.full_name = full_name
            updated = True
        if updated:
            db.commit()
            db.refresh(user)
        return user

    user = User(
        email=email,
        full_name=full_name,
        role=role,
        department=department,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    x_user_role: Optional[str] = Header(default=None),
    x_user_email: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    """Resolve current user via JWT Bearer token or enterprise dev RBAC headers."""
    settings = get_settings()

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        claims = decode_access_token(token)
        role = claims["role"]
        email = claims.get("email") or claims["sub"]
        full_name = claims.get("full_name") or DEFAULT_PERSONAS[role]["full_name"]
        department = claims.get("department") or DEFAULT_PERSONAS[role]["department"]
        user = _upsert_user(db, email=email, full_name=full_name, role=role, department=department)
        return AuthenticatedUser(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            department=user.department,
            auth_method="jwt",
        )

    if not settings.allow_header_role_auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token authentication is required.",
        )

    role = (x_user_role or "employee").strip().lower()
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Invalid role '{role}'. Allowed roles: {sorted(VALID_ROLES)}.",
        )

    persona = DEFAULT_PERSONAS[role]
    email = (x_user_email or persona["email"]).strip().lower()
    full_name = (x_user_name or persona["full_name"]).strip()
    department = persona["department"]

    user = _upsert_user(db, email=email, full_name=full_name, role=role, department=department)
    return AuthenticatedUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        department=user.department,
        auth_method="header",
    )


def require_roles(*allowed_roles: str) -> Callable[..., AuthenticatedUser]:
    """Dependency factory enforcing RBAC role authorization and writing denial audit logs."""
    allowed = set(allowed_roles)

    def _checker(
        current_user: AuthenticatedUser = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> AuthenticatedUser:
        if current_user.role not in allowed:
            db.add(
                AuditLog(
                    event_type="rbac_denied",
                    severity="warning",
                    actor_email=current_user.email,
                    actor_role=current_user.role,
                    action=f"Access denied: role '{current_user.role}' lacks permissions {sorted(allowed)}",
                    details_json={
                        "required_roles": sorted(allowed),
                        "actual_role": current_user.role,
                    },
                )
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{current_user.role}' is not authorized for this resource. "
                    f"Required role: {', '.join(sorted(allowed))}."
                ),
            )
        return current_user

    return _checker


def record_rbac_denial(
    db: Session,
    user: AuthenticatedUser,
    action: str,
    session_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """Persist an audit log entry when a resource-level authorization check fails."""
    db.add(
        AuditLog(
            event_type="rbac_denied",
            severity="warning",
            actor_email=user.email,
            actor_role=user.role,
            session_id=session_id,
            action=action,
            details_json=details or {},
        )
    )
    db.commit()
