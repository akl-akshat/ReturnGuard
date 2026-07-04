"""Demo-tenant seeding for hosted deployments (opt-in via ``RG_SEED_DEMO=1``).

Free-tier hosts give the app an ephemeral disk, so the SQLite chat/tenant store resets on
every deploy. To keep the public demo self-explanatory, startup (when opted in) ensures a
sample company with an uploaded policy document exists — so anyone opening the live link can
bind a session to "Zomato (demo)" and watch the per-tenant RAG ground the agent's answers.

Idempotent: re-uploading the same doc name replaces it, and an existing company is reused.
Never used in tests (the flag is off by default) and never blocks boot (best-effort).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("service.demo_seed")

DEMO_COMPANY = "Zomato (demo)"
DEMO_DOC = "zomato-refund-policy.md"
DEMO_POLICY = """# Zomato Refund & Replacement Policy (Demo)

## Spoiled or contaminated food
If a customer receives food that is spoiled, stale, or contains a foreign object (hair,
insect, plastic), they must report it within 2 hours of delivery with a clear photo. On
verification, Zomato issues a full refund to the original payment source within 24 hours.
No replacement is offered for hygiene incidents.

## Wrong or missing items
If an item is missing or a wrong dish was delivered, the customer may choose a redelivery of
the correct item or a refund of that item's price. A photo of the received order is required.

## Late delivery
Orders delivered more than 20 minutes after the promised time earn a coupon worth 20% of the
order value, capped at Rs 150. No cash refunds for delays.

## Escalation
Any claim above Rs 1000, or a customer with more than 2 refunds in 30 days, must be reviewed
by a support specialist before any payout.
"""


def seed_enabled() -> bool:
    return os.environ.get("RG_SEED_DEMO", "").strip() in ("1", "true", "yes")


def ensure_demo_tenant() -> None:
    """Create the demo company + policy document if missing (best-effort)."""
    try:
        from service import policy_store

        co = policy_store.get_company_by_name(DEMO_COMPANY) or policy_store.create_company(DEMO_COMPANY)
        if not any(d["doc_name"] == DEMO_DOC for d in policy_store.list_documents(co["id"])):
            out = policy_store.upload_policy(co["id"], DEMO_DOC, DEMO_POLICY)
            log.info("Seeded demo tenant %r with %s chunks", DEMO_COMPANY, out["chunks"])
    except Exception:  # noqa: BLE001 — demo convenience must never block boot
        log.warning("Demo-tenant seeding failed; continuing without it", exc_info=True)
