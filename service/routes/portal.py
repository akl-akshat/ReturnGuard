"""Customer portal + operator console (UI-1 chat surface, UI-2 console) and the small
read-only endpoints they need. These serve the human-facing product; the agent logic and
all guardrails are unchanged behind them.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from agent.deps import get_deps
from policies.retrieve import within_return_window

router = APIRouter()
_STATIC = Path(__file__).resolve().parents[1] / "static"

# Demo "signed-in" customers, mapped to seeded profiles. The customer UI never shows risk.
DEMO_CUSTOMERS = [
    {"id": "CUST-LOW1", "name": "Aarav Sharma", "initials": "AS"},
    {"id": "CUST-VIP1", "name": "Diya Mehta", "initials": "DM"},
    {"id": "CUST-NEW1", "name": "Rohan Das", "initials": "RD"},
    {"id": "CUST-SERIAL", "name": "Vikram Rao", "initials": "VR"},
]

_EMOJI = {"apparel": "👕", "footwear": "👟", "electronics": "🎧", "home": "🛋️",
          "books": "📚", "beauty": "💄", "innerwear": "🩲", "grocery": "🛒"}


@router.get("/", response_class=HTMLResponse)
def landing() -> str:
    return (_STATIC / "landing.html").read_text(encoding="utf-8")


@router.get("/chat", response_class=HTMLResponse)
def customer_portal() -> str:
    return (_STATIC / "chat.html").read_text(encoding="utf-8")


@router.get("/admin", response_class=HTMLResponse)
def operator_console() -> str:
    return (_STATIC / "admin.html").read_text(encoding="utf-8")


@router.get("/client", response_class=HTMLResponse)
def client_console() -> str:
    """Per-client (brand) portal: their review queue, order DB and policy documents."""
    return (_STATIC / "client.html").read_text(encoding="utf-8")


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_alias() -> str:
    return (_STATIC / "admin.html").read_text(encoding="utf-8")


@router.get("/api/customers")
def list_demo_customers() -> list[dict]:
    return DEMO_CUSTOMERS


@router.get("/api/customers/{customer_id}/orders")
def customer_orders(customer_id: str) -> list[dict]:
    repo = get_deps().repo
    if not repo.get_customer(customer_id):
        raise HTTPException(status_code=404, detail="customer not found")
    out = []
    for o in repo.get_orders_for_customer(customer_id):
        rwe = o.return_window_end
        out.append({
            "id": o.id,
            "title": o.title,
            "category": o.category,
            "emoji": _EMOJI.get(o.category, "📦"),
            "price": float(o.price),
            "payment_mode": o.payment_mode,
            "delivery_date": o.delivery_date.isoformat() if o.delivery_date else None,
            "return_window_end": rwe.isoformat() if rwe else None,
            "within_window": within_return_window(rwe) if rwe else False,
            "returnable": rwe is not None,
        })
    # show the nicely-titled fixture orders first
    out.sort(key=lambda r: (not r["id"].startswith("ORD-"), r["id"]))
    return out
