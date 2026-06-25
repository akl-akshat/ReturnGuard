"""Customer reply composition (FR-RES-1..3, NFR-CMP-1/2).

Deterministic, policy-consistent templates that:
* reflect the executed action faithfully (never contradict it),
* for deflection offers, state the customer's right to a standard refund (NFR-CMP-1), and
* never reveal risk scores or fraud reasoning (NFR-CMP-2) — by construction the templates
  contain no risk vocabulary, and :func:`scrub` is a defensive backstop.

In ``anthropic`` mode the responder may instead ask Claude with a leak-forbidding system
prompt; the templates remain the safe, reproducible default used offline and in eval.
"""

from __future__ import annotations

from typing import Any

_FORBIDDEN = ("risk", "fraud", "serial", "wardrob", "abuse", "score", "blacklist")

_REFUND_RIGHT = " You're also entitled to a standard refund to your original payment method if you'd prefer."


def _amt(action: dict[str, Any]) -> str:
    a = action.get("amount") or 0.0
    return f"₹{a:.0f}"


def compose_reply(state: dict[str, Any]) -> str:
    status = state.get("status")
    if status == "not_found":
        return ("We're sorry, but we couldn't locate an order matching those details. "
                "Could you please double-check and share your order ID so we can help?")
    if state.get("requires_human") and not state.get("human_decision"):
        return ("Thanks for reaching out. Your request needs a quick review by our team and "
                "we'll get back to you shortly with a resolution.")
    if state.get("human_decision") == "reject":
        return _denial(state)

    action = state.get("executed_action") or state.get("proposed_action") or {}
    at = action.get("action_type")
    if at == "exchange_with_size_guide":
        return ("Sorry the fit wasn't right! We've set up a free size exchange and included a "
                "size guide to help you pick the perfect fit." + _REFUND_RIGHT)
    if at == "free_exchange":
        return "We've arranged a free exchange for you." + _REFUND_RIGHT
    if at == "expedited_replacement":
        extra = " We've also added a small goodwill credit for the inconvenience." if action.get("params", {}).get("goodwill") else ""
        return ("We're really sorry your item arrived faulty. We've arranged a priority "
                "replacement at no cost to you." + extra)
    if at == "instant_refund":
        return f"We've processed a full refund of {_amt(action)} to your original payment method. It should reflect shortly."
    if at == "partial_refund":
        return f"We've processed a partial refund of {_amt(action)} to your original payment method."
    if at == "store_credit_refund":
        return f"We've issued {_amt(action)} as store credit to your account." + _REFUND_RIGHT
    if at == "retention_coupon":
        return (f"We'd love for you to keep your order — here's a {_amt(action)} coupon as a thank-you." + _REFUND_RIGHT)
    if at == "goodwill_credit":
        return f"Thanks for your patience — we've added a {_amt(action)} goodwill credit to your account."
    if at == "deny_with_explanation":
        return _denial(state)
    if at == "provide_information":
        return ("Thanks for reaching out — here's the information about your order and the "
                "applicable policy. Let us know if there's anything else we can help with.")
    return "Thanks for reaching out — we've recorded your request and will follow up shortly."


def _denial(state: dict[str, Any]) -> str:
    within = state.get("within_return_window")
    reason = "the return window for this order has closed" if within is False else "this item isn't eligible for return under our policy"
    return (f"Thanks for reaching out. After reviewing our return policy, {reason}, so we're "
            "unable to process a return in this case. We're sorry for the inconvenience and "
            "are happy to help with anything else.")


def scrub(message: str) -> str:
    """Defensive backstop: never let internal risk vocabulary reach the customer."""
    lowered = message.lower()
    if any(term in lowered for term in _FORBIDDEN):
        return ("Thanks for reaching out — we've reviewed your request and will follow up with "
                "the appropriate resolution shortly.")
    return message
