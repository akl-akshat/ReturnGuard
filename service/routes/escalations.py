"""Escalation endpoints: list the pending queue and submit a reviewer decision (API-2)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from agent.deps import get_deps
from agent.runner import is_paused, resume, run_config
from service.schemas import DecisionRequest, EscalationOut, ResolveResponse, summarize

router = APIRouter()


@router.get("/escalations", response_model=list[EscalationOut])
def list_escalations() -> list[EscalationOut]:
    return [EscalationOut(**e) for e in get_deps().repo.list_escalations("pending")]


@router.post("/escalations/{request_id}/decision", response_model=ResolveResponse)
def decide(request_id: str, body: DecisionRequest, request: Request) -> ResolveResponse:
    graph = request.app.state.graph
    cfg = run_config(request_id)

    # API-2: reject a decision for a request_id that is not currently awaiting a human.
    if not is_paused(graph, cfg):
        raise HTTPException(status_code=409, detail="request is not awaiting a human decision")

    # Defence-in-depth (the guardrail remains the real net): require a recorded reviewer
    # identity (NFR-SEC-4) and reject an invalid modified amount up front (D-02).
    if not body.reviewer_id:
        raise HTTPException(status_code=422, detail="reviewer_id is required")
    if body.modified_action and (body.modified_action.get("amount") or 0) < 0:
        raise HTTPException(status_code=422, detail="modified_action.amount must be non-negative")

    final = resume(graph, cfg, body.model_dump())
    return summarize(final, paused=False)
