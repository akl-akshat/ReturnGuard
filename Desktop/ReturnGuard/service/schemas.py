"""Pydantic request/response models for the FastAPI service (API-1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ResolveRequest(BaseModel):
    request_id: str = Field(..., min_length=1)
    issue_text: str = Field(..., min_length=1)
    order_id: str | None = None
    customer_id: str | None = None
    channel: Literal["chat", "api", "kafka_event"] = "api"


class ResolveResponse(BaseModel):
    request_id: str
    status: str
    paused: bool = False
    reply: str | None = None
    issue_type: str | None = None
    root_cause: str | None = None
    action_type: str | None = None
    amount: float | None = None
    requires_human: bool = False
    guardrail_status: str | None = None
    expected_saving: float | None = None
    rationale: str | None = None
    trace_id: str | None = None


class DecisionRequest(BaseModel):
    decision: Literal["approve", "modify", "reject"]
    modified_action: dict[str, Any] | None = None
    reviewer_id: str | None = None


class EscalationOut(BaseModel):
    request_id: str
    status: str
    recommendation: dict[str, Any]
    assigned_to: str | None = None


def summarize(state: dict[str, Any], paused: bool = False) -> ResolveResponse:
    action = state.get("executed_action") or state.get("proposed_action") or {}
    return ResolveResponse(
        request_id=state["request_id"],
        status=("awaiting_human" if paused else state.get("status", "pending")),
        paused=paused,
        reply=state.get("customer_message"),
        issue_type=state.get("issue_type"),
        root_cause=state.get("root_cause"),
        action_type=action.get("action_type"),
        amount=action.get("amount"),
        requires_human=state.get("requires_human", False),
        guardrail_status=state.get("guardrail_status"),
        expected_saving=state.get("expected_saving"),
        rationale=state.get("rationale"),
        trace_id=state.get("trace_id"),
    )


def summarize_resolution(row: dict[str, Any]) -> ResolveResponse:
    action = row.get("executed_action") or row.get("proposed_action") or {}
    return ResolveResponse(
        request_id=row["request_id"],
        status=row.get("status", "pending"),
        paused=row.get("status") == "escalated",
        reply=row.get("customer_message"),
        issue_type=row.get("issue_type"),
        root_cause=row.get("root_cause"),
        action_type=action.get("action_type") if isinstance(action, dict) else None,
        amount=row.get("amount"),
        requires_human=row.get("requires_human", False),
        guardrail_status=row.get("guardrail_status"),
        expected_saving=row.get("expected_saving"),
        rationale=row.get("rationale"),
        trace_id=row.get("trace_id"),
    )
