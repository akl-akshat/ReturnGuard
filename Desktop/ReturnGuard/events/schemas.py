"""Versioned Kafka event contracts (SRS §5.4). All messages JSON, keyed by request_id."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RequestEvent(BaseModel):
    """returns.requests.v1 (ingest)."""

    request_id: str = Field(..., min_length=1)
    order_id: str | None = None
    customer_id: str | None = None
    issue_text: str = Field(..., min_length=1)
    issue_type_hint: str | None = None
    source: str = "kafka"
    timestamp: str | None = None


class ResolutionEvent(BaseModel):
    """returns.resolutions.v1 (emit)."""

    request_id: str
    order_id: str | None = None
    customer_id: str | None = None
    issue_type: str | None = None
    root_cause: str | None = None
    risk_score: float | None = None
    action_type: str | None = None
    amount: float = 0.0
    requires_human: bool = False
    rationale: str | None = None
    expected_saving: float = 0.0
    status: str = "resolved"
    timestamp: str | None = None


class EscalationEvent(BaseModel):
    """returns.escalations.v1 (emit): resolution payload + a recommendation object."""

    request_id: str
    status: str = "pending"
    recommendation: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    """returns.audit.v1 (emit): a mirror of each audit_log insert."""

    request_id: str
    action_type: str
    amount: float | None = None
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class OutcomeEvent(BaseModel):
    """returns.outcomes.v1 (emit, optional)."""

    request_id: str
    status: str
    timestamp: str | None = None
