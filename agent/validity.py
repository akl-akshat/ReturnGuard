"""Issue-type ⇄ product-category validity, and evidence requirements per claim.

A size/fit complaint about a television is nonsensical; you cannot have a "wrong size"
problem with an item that has no size dimension. This module encodes which issue types can
legitimately apply to which product categories, and which claims must be backed by evidence
(a photo / proof) before any money-moving remedy is offered.

Both are deliberately conservative: the point of the redesign is that the agent must be
*sure* an issue is real before it acts, and a claim that cannot even apply to the product is
the cheapest possible thing to reject.
"""

from __future__ import annotations

# Categories where physical size / fit is a real dimension of the product.
WEARABLE = {"apparel", "footwear", "innerwear", "jewelry"}
# Consumable / perishable categories where a food-quality claim (spoiled, burnt, foreign
# object) can apply.
PERISHABLE = {"grocery"}

# issue_type -> the set of categories it can validly apply to. "*" means "any category".
ISSUE_CATEGORY_VALIDITY: dict[str, set[str]] = {
    "wrong_size": WEARABLE,          # only wearables have a size/fit
    "quality_complaint": {"*"},      # anything can be "not as described / poor quality"
    "damaged_item": {"*"},           # anything can arrive physically damaged
    "wrong_item": {"*"},             # any parcel can contain the wrong thing
    "missing_item": {"*"},           # any parcel can be short
    "late_delivery": {"*"},
    "cancel_request": {"*"},
    "refund_status": {"*"},
    "return_request": {"*"},
    "other": {"*"},
}


def issue_valid_for_category(issue_type: str, category: str) -> bool:
    """True if ``issue_type`` can legitimately apply to a product in ``category``."""
    allowed = ISSUE_CATEGORY_VALIDITY.get(issue_type, {"*"})
    return "*" in allowed or category in allowed


# Root causes / issue types that must be backed by evidence before a money-moving remedy is
# offered. These are the claims a customer could fabricate for gain, so we verify first.
_EVIDENCE_ROOT_CAUSES = {"defect_damage", "wrong_item_shipped", "size_fit_mismatch"}
_EVIDENCE_ISSUE_TYPES = {"damaged_item", "quality_complaint", "wrong_item", "wrong_size"}


def evidence_required(issue_type: str, root_cause: str) -> bool:
    """Whether this claim must be substantiated (photo/proof) before we act on it."""
    return root_cause in _EVIDENCE_ROOT_CAUSES or issue_type in _EVIDENCE_ISSUE_TYPES


def evidence_kind(issue_type: str, category: str, root_cause: str) -> str:
    """A human-readable description of the evidence we should ask the customer for."""
    if category in PERISHABLE:
        return "a clear photo of the item showing the problem (spoilage, damage, or foreign object)"
    if root_cause == "size_fit_mismatch" or issue_type == "wrong_size":
        return "a photo of you wearing it, or the garment laid flat next to a tape measure"
    if root_cause == "wrong_item_shipped" or issue_type == "wrong_item":
        return "a photo of the item you received together with its packing label"
    if root_cause == "defect_damage" or issue_type in ("damaged_item", "quality_complaint"):
        return "a photo (or a short video) that clearly shows the defect"
    return "a photo that shows the problem"


def redirect_message(issue_type: str, category: str, item_title: str) -> str:
    """Message for when the claimed issue cannot apply to this product's category."""
    if issue_type == "wrong_size":
        return (
            f"Hmm — a size or fit issue doesn't apply to your **{item_title}** ({category}). "
            "Did something else go wrong? For example: it arrived **damaged**, it's **faulty / not "
            "working**, you got the **wrong item**, or part of the order is **missing**. "
            "Tell me what actually happened and I'll sort it out."
        )
    return (
        f"That doesn't quite fit your **{item_title}** ({category}). Could you tell me what "
        "actually went wrong — is it **damaged**, **faulty**, the **wrong item**, **missing**, "
        "or a **delivery** problem?"
    )
