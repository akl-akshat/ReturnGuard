"""RBAC sessions with a real credential chain.

* **admin / client / rep** log in with **ID + password** (credentials are provisioned down the
  chain: admin → client → employees; see ``service.auth_store``).
* **customers** use the demo phone-identity sign-in (no password in the demo).

The session is a signed-shape cookie (``role:entity_id``) the portals read; ``require_role``
guards mutating endpoints (registration, credential issuing, credibility overrides).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

from agent.deps import get_deps
from service import auth_store, platform_store, policy_store, rep_store

router = APIRouter()

_REDIRECT = {"customer": "/app", "client": "/client", "rep": "/rep", "admin": "/admin"}
_COOKIE = "rg_session"


class Login(BaseModel):
    role: Literal["customer", "client", "rep", "admin"]
    id: str = Field(default="", max_length=80)          # customer id, or login_id for staff roles
    password: str = Field(default="", max_length=128)


def _entity(role: str, entity_id: str) -> dict | None:
    if role == "admin":
        return {"id": "admin", "name": "Platform Admin", "role": "admin"}
    if role == "customer":
        u = platform_store.get_user(entity_id) or (get_deps().repo.get_customer(entity_id) and
                                                   {"id": entity_id, "name": entity_id})
        if isinstance(u, dict):
            return {"id": u["id"], "name": u.get("name", u["id"]), "role": "customer",
                    "phone": u.get("phone")}
        return None
    if role == "client":
        co = policy_store.get_company(entity_id)
        return {"id": co["id"], "name": co["name"], "role": "client"} if co else None
    if role == "rep":
        rep = rep_store.get_rep(entity_id)
        return ({"id": rep["id"], "name": rep["name"], "role": "rep",
                 "company_id": rep["company_id"]} if rep else None)
    return None


@router.post("/api/auth/login")
def login(body: Login, response: Response) -> dict:
    if body.role == "customer":
        who = _entity("customer", body.id)
        if not who:
            raise HTTPException(status_code=404, detail="customer not found")
    else:
        cred = auth_store.verify(body.id, body.password)
        if not cred or cred["role"] != body.role:
            raise HTTPException(status_code=401, detail="invalid ID or password")
        who = _entity(body.role, cred["entity_id"])
        if not who:
            raise HTTPException(status_code=404, detail="account entity no longer exists")
    response.set_cookie(_COOKIE, f"{who['role']}:{who['id']}", max_age=7 * 24 * 3600,
                        samesite="lax", path="/")
    return {"ok": True, "redirect": _REDIRECT[body.role], **who}


@router.get("/api/auth/me")
def me(rg_session: str | None = Cookie(default=None)) -> dict:
    if not rg_session or ":" not in rg_session:
        return {"authenticated": False}
    role, ident = rg_session.split(":", 1)
    who = _entity(role, ident)
    return {"authenticated": bool(who), **(who or {})}


@router.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(_COOKIE, path="/")
    return {"ok": True}


# --------------------------------------------------------------- role guard
def require_role(rg_session: str | None, *roles: str) -> dict:
    """Resolve the cookie session and require one of ``roles`` — 401/403 otherwise."""
    if not rg_session or ":" not in rg_session:
        raise HTTPException(status_code=401, detail="sign in first")
    role, ident = rg_session.split(":", 1)
    who = _entity(role, ident)
    if not who:
        raise HTTPException(status_code=401, detail="session no longer valid")
    if roles and who["role"] not in roles:
        raise HTTPException(status_code=403, detail=f"requires role: {' or '.join(roles)}")
    return who
