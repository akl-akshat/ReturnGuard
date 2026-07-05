"""Representatives, complaint assignment, weekly complaint stats, customer notifications."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from service import chat_store, policy_store, rep_store

router = APIRouter()


class CreateRep(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)


class Assign(BaseModel):
    rep_id: str = Field(..., min_length=3)


class Availability(BaseModel):
    available: bool


# ------------------------------------------------------------------ reps
@router.get("/api/companies/{company_id}/reps")
def company_reps(company_id: str) -> list[dict]:
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    return rep_store.reps_for_company(company_id)


@router.post("/api/companies/{company_id}/reps")
def add_rep(company_id: str, body: CreateRep) -> dict:
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    return rep_store.add_rep(company_id, body.name)


@router.post("/api/reps/{rep_id}/availability")
def set_availability(rep_id: str, body: Availability) -> dict:
    if not rep_store.get_rep(rep_id):
        raise HTTPException(status_code=404, detail="rep not found")
    rep_store.set_available(rep_id, body.available)
    return {"ok": True}


@router.post("/api/sessions/{session_id}/assign")
def assign_session(session_id: str, body: Assign) -> dict:
    """Assign an escalated complaint to one of the brand's available representatives."""
    s = chat_store.get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    if s.get("status") != "escalated":
        raise HTTPException(status_code=409, detail="only escalated cases can be assigned")
    rep = rep_store.get_rep(body.rep_id)
    if not rep or rep["company_id"] != s.get("company_id"):
        raise HTTPException(status_code=404, detail="rep not found for this company")
    rep_store.assign(session_id, rep["id"], rep["company_id"])
    return {"ok": True, "session_id": session_id, "rep": rep}


@router.get("/api/sessions/{session_id}/assignment")
def session_assignment(session_id: str) -> dict:
    a = rep_store.get_assignment(session_id)
    if not a:
        raise HTTPException(status_code=404, detail="not assigned")
    return a


@router.get("/api/reps/{rep_id}/queue")
def rep_queue(rep_id: str) -> list[dict]:
    """The complaints assigned to THIS representative (their whole portal view)."""
    if not rep_store.get_rep(rep_id):
        raise HTTPException(status_code=404, detail="rep not found")
    ids = set(rep_store.sessions_for_rep(rep_id))
    out = []
    for s in chat_store.list_reviews():
        if s["id"] not in ids:
            continue
        st = s.get("state") or {}
        out.append({
            "id": s["id"], "customer_id": s["customer_id"], "title": s["title"],
            "reason": st.get("escalation_reason"), "issue_type": st.get("issue_type"),
            "proposed_action": st.get("proposed_action"), "evidence": st.get("evidence"),
            "policy_citations": st.get("policy_citations"), "updated_at": s["updated_at"],
        })
    return out


# ------------------------------------------------------------------ weekly stats
@router.get("/api/companies/{company_id}/stats/weekly")
def weekly_stats(company_id: str) -> dict:
    """Per-day complaints raised / resolved / pending for the last 7 days (client chart)."""
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    sessions = chat_store.sessions_for_company(company_id)
    now = time.time()
    days: list[dict] = []
    for d in range(6, -1, -1):
        start = now - (d + 1) * 86400
        end = now - d * 86400
        label = time.strftime("%a", time.localtime(end - 43200))
        raised = sum(1 for s in sessions if start < s["created_at"] <= end)
        resolved = sum(1 for s in sessions
                       if s["status"] in ("resolved", "denied") and start < s["updated_at"] <= end)
        days.append({"day": label, "raised": raised, "resolved": resolved})
    pending = sum(1 for s in sessions if s["status"] == "escalated")
    open_now = sum(1 for s in sessions if s["status"] in ("open", "awaiting_confirmation",
                                                          "awaiting_evidence"))
    total_resolved = sum(1 for s in sessions if s["status"] == "resolved")
    return {"days": days, "pending_review": pending, "open": open_now,
            "resolved_total": total_resolved, "total": len(sessions)}


# ------------------------------------------------------------------ notifications
@router.get("/api/platform/users/{user_id}/notifications")
def notifications(user_id: str) -> list[dict]:
    """Latest agent/specialist messages across the customer's conversations — the dashboard's
    'what's new' rail (like classroom updates in an ERP)."""
    return chat_store.recent_updates_for(user_id)
