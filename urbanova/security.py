"""Minimal Phase 1 authentication and project authorization (Module 1, B.6).

Local accounts with scrypt password hashing and opaque bearer tokens stored only
as SHA-256 hashes. Project access is role based: owner > editor > viewer.
Organisations, SSO and MFA are outside this build (see docs/DECISIONS.md).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from . import db
from .settings import settings

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}
MAX_FAILED_LOGINS = 8
LOCKOUT_MINUTES = 15
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest.hex(), digest_hex)


def validate_new_account(email: str, name: str, password: str) -> None:
    if not EMAIL_RE.match(email or ""):
        raise AuthError("Enter a valid email address.", 422)
    if not (name or "").strip():
        raise AuthError("Enter your name.", 422)
    if len(password or "") < 10:
        raise AuthError("Use a password of at least 10 characters.", 422)


def register(email: str, name: str, password: str) -> dict:
    email = (email or "").strip().lower()
    validate_new_account(email, name, password)
    if db.get("users", email, key="email"):
        raise AuthError("An account with this email already exists. Sign in instead.", 409)
    user = {
        "id": db.new_id("usr"), "email": email, "name": name.strip(),
        "password_hash": hash_password(password), "created_at": db.now(),
    }
    db.insert("users", user)
    return public_user(user)


def public_user(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"], "name": user["name"]}


def login(email: str, password: str) -> tuple[str, dict]:
    email = (email or "").strip().lower()
    user = db.get("users", email, key="email")
    generic = AuthError("Email or password is incorrect.")
    if not user:
        raise generic
    if user.get("locked_until") and user["locked_until"] > db.now():
        raise AuthError("Too many failed sign-in attempts. Try again in a few minutes.", 429)
    if not verify_password(password or "", user["password_hash"]):
        failed = (user.get("failed_logins") or 0) + 1
        upd = {"failed_logins": failed}
        if failed >= MAX_FAILED_LOGINS:
            upd = {"failed_logins": 0, "locked_until": (datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)).isoformat(timespec="seconds")}
        db.update("users", "id", user["id"], upd)
        raise generic
    db.update("users", "id", user["id"], {"failed_logins": 0, "locked_until": None})
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    db.insert("sessions", {
        "token_hash": _token_hash(token), "user_id": user["id"], "created_at": db.now(),
        "expires_at": expires.isoformat(timespec="seconds"),
    })
    return token, public_user(user)


def logout(token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token_hash = ?", [_token_hash(token)])


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_from_token(token: str | None) -> dict:
    if not token:
        raise AuthError("Sign in to continue.")
    sess = db.get("sessions", _token_hash(token), key="token_hash")
    if not sess or sess["expires_at"] < db.now():
        raise AuthError("Your session has expired. Sign in again.")
    user = db.get("users", sess["user_id"])
    if not user:
        raise AuthError("Account not found.")
    return public_user(user)


def project_role(project_id: str, user_id: str) -> str | None:
    row = db.select("project_members", "project_id = ? AND user_id = ?", [project_id, user_id])
    return row[0]["role"] if row else None


def require_role(project_id: str, user: dict, minimum: str) -> str:
    """Return the user's role or raise 404 (do not reveal that a project exists) / 403."""
    project = db.get("projects", project_id)
    role = project_role(project_id, user["id"]) if project else None
    if not project or role is None:
        raise AuthError("Project not found.", 404)
    if ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise AuthError(f"Your role on this project ({role}) does not allow this action.", 403)
    return role
