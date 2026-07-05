"""Lightweight RBAC session for the demo: pick a role + identity, get a cookie, land on the
right dashboard. Not real authentication (no passwords) — it demonstrates role-based access and
routing (customer / client / representative / admin), which is what the platform UX needs.

In production this is a JWT with hashed credentials and `withRole` guards on every route; the
shape here (login -> role-scoped session -> role dashboard) mirrors that.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from agent.deps import get_deps
from service import platform_store, policy_store, rep_store

router = APIRouter()

_REDIRECT = {"customer": "/app", "client": "/client", "rep": "/rep", "admin": "/admin"}
_COOKIE = "rg_session"


class Login(BaseModel):
    role: Literal["customer", "client", "rep", "admin"]
    id: str = Field(default="", max_length=80)


def _identity(role: str, ident: str) -> dict | None:
    if role == "admin":
        return {"id": "admin", "name": "Platform Admin", "role": "admin"}
    if role == "customer":
        u = platform_store.get_user(ident) or (get_deps().repo.get_customer(ident) and
                                               {"id": ident, "name": ident})
        if isinstance(u, dict):
            return {"id": u["id"], "name": u.get("name", u["id"]), "role": "customer",
                    "phone": u.get("phone")}
        return None
    if role == "client":
        co = policy_store.get_company(ident)
        return {"id": co["id"], "name": co["name"], "role": "client"} if co else None
    if role == "rep":
        rep = rep_store.get_rep(ident)
        return ({"id": rep["id"], "name": rep["name"], "role": "rep",
                 "company_id": rep["company_id"]} if rep else None)
    return None


@router.post("/api/auth/login")
def login(body: Login, response: Response) -> dict:
    who = _identity(body.role, body.id)
    if not who:
        raise HTTPException(status_code=404, detail=f"no {body.role} identity for {body.id!r}")
    token = f"{body.role}:{who['id']}"
    response.set_cookie(_COOKIE, token, max_age=7 * 24 * 3600, samesite="lax", path="/")
    return {"ok": True, "redirect": _REDIRECT[body.role], **who}


@router.get("/api/auth/me")
def me(rg_session: str | None = None) -> dict:
    # cookie is read by the pages directly for the demo; this endpoint validates/echoes it
    if not rg_session or ":" not in rg_session:
        return {"authenticated": False}
    role, ident = rg_session.split(":", 1)
    who = _identity(role, ident)
    return {"authenticated": bool(who), **(who or {})}


@router.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(_COOKIE, path="/")
    return {"ok": True}
