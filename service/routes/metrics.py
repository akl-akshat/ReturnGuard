"""Analytics endpoint (FR-RPT-1). The UIs are served by service.routes.portal (UI-1/UI-2)."""

from __future__ import annotations

from fastapi import APIRouter

from agent.deps import get_deps
from service.metrics import compute_summary

router = APIRouter()


@router.get("/metrics/summary")
def metrics_summary() -> dict:
    return compute_summary(get_deps().repo)
