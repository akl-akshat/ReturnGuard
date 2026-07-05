"""Platform-admin endpoints: client onboarding and credibility governance.

Only the admin can register a client company. Registration is the full onboarding in one
call: company + its policy document (indexed for RAG) + the client's login credential
(generated password, returned exactly once). The new client is immediately live everywhere —
sign-in page, client portal, order DB, policy grounding.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie
from pydantic import BaseModel, Field

from service import auth_store, chat_store, platform_store, policy_store, rating_store
from service.routes.auth import require_role

router = APIRouter()


class RegisterCompany(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    doc_name: str = Field(default="policy.md", min_length=1, max_length=120)
    policy_text: str = Field(..., min_length=40, max_length=400_000)
    login_id: str | None = Field(default=None, max_length=60)


class SetScore(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0)


@router.post("/api/admin/companies")
def register_company(body: RegisterCompany, rg_session: str | None = Cookie(default=None)) -> dict:
    who = require_role(rg_session, "admin")
    co = policy_store.create_company(body.name)
    doc = policy_store.upload_policy(co["id"], body.doc_name, body.policy_text)
    cred = auth_store.issue_credential(
        "client", co["id"], (body.login_id or auth_store.slugify(body.name)),
        created_by=f"admin:{who['id']}")
    return {"company": co, "policy": doc,
            "credentials": cred,   # shown once — the admin hands these to the client
            "note": "Share the login with the client; they can then add employees and orders."}


@router.get("/api/admin/customers")
def customers_overview(rg_session: str | None = Cookie(default=None)) -> list[dict]:
    require_role(rg_session, "admin")
    out = []
    for u in platform_store.list_users():
        cred = chat_store.get_credibility(u["id"]) or {}
        out.append({**u, "score": cred.get("score", 0.75),
                    "denied": cred.get("denied_count", 0), "false": cred.get("false_count", 0),
                    "events": rating_store.events_for(u["id"], limit=5)})
    return out


@router.post("/api/admin/customers/{user_id}/score")
def set_score(user_id: str, body: SetScore, rg_session: str | None = Cookie(default=None)) -> dict:
    who = require_role(rg_session, "admin")
    if not platform_store.get_user(user_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="customer not found")
    return rating_store.admin_set_score(user_id, body.score, actor=f"admin:{who['id']}")


@router.get("/api/companies/{company_id}/records")
def company_records(company_id: str, rg_session: str | None = Cookie(default=None)) -> list[dict]:
    """Everything that happened on this brand's cases — the client's returns/refunds ledger."""
    out = []
    for s in chat_store.sessions_for_company(company_id):
        st = s.get("state") or {}
        res = st.get("resolution")
        out.append({
            "session_id": s["id"], "order_id": s.get("order_id"), "title": s["title"],
            "customer_id": s["customer_id"], "status": s["status"],
            "action_type": (res or {}).get("action_type"),
            "amount": (res or {}).get("amount") or 0,
            "rated": rating_store.has_rating(s["id"]),
            "updated_at": s["updated_at"],
        })
    return out
