"""Customer wallet + rewards API — refunds, interest, withdrawals, coupons, games."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from service import platform_store, wallet_store

router = APIRouter()


class Amount(BaseModel):
    amount: float = Field(..., gt=0, le=10_00_000)


class CouponReq(BaseModel):
    brand: str = Field(..., min_length=1, max_length=60)
    amount: float = Field(..., gt=0, le=10_00_000)


class LotteryReq(BaseModel):
    lottery: Literal["dinner", "gadget"] = "dinner"


def _require_user(user_id: str) -> None:
    if not platform_store.get_user(user_id):
        raise HTTPException(status_code=404, detail="customer not found")


@router.get("/api/wallet/{user_id}")
def wallet(user_id: str) -> dict:
    _require_user(user_id)
    w = wallet_store.get_wallet(user_id)
    return {**w, "transactions": wallet_store.transactions(user_id),
            "coupons": wallet_store.coupons(user_id)}


@router.post("/api/wallet/{user_id}/deposit")
def deposit(user_id: str, body: Amount) -> dict:
    _require_user(user_id)
    return {**wallet_store.deposit(user_id, body.amount), "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/kyc")
def kyc(user_id: str) -> dict:
    _require_user(user_id)
    wallet_store.set_kyc(user_id, True)
    return {"ok": True, "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/withdraw")
def withdraw(user_id: str, body: Amount) -> dict:
    _require_user(user_id)
    out = wallet_store.withdraw(user_id, body.amount)
    if not out["ok"] and out.get("reason") == "kyc_required":
        raise HTTPException(status_code=412, detail="kyc_required")
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out.get("reason", "withdrawal failed"))
    return {**out, "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/coupon")
def redeem_coupon(user_id: str, body: CouponReq) -> dict:
    _require_user(user_id)
    out = wallet_store.redeem_coupon(user_id, body.brand, body.amount)
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out.get("reason", "redeem failed"))
    return {**out, "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/coupon/{code}/reveal")
def reveal_coupon(user_id: str, code: str) -> dict:
    _require_user(user_id)
    out = wallet_store.reveal_coupon(user_id, code)
    if not out["ok"]:
        raise HTTPException(status_code=404, detail="coupon not found")
    return out


@router.post("/api/wallet/{user_id}/spin")
def spin(user_id: str) -> dict:
    _require_user(user_id)
    out = wallet_store.spin_wheel(user_id)
    return {**out, "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/daily")
def daily(user_id: str) -> dict:
    _require_user(user_id)
    out = wallet_store.daily_reward(user_id)
    return {**out, "wallet": wallet_store.get_wallet(user_id)}


@router.post("/api/wallet/{user_id}/lottery")
def lottery(user_id: str, body: LotteryReq) -> dict:
    _require_user(user_id)
    out = wallet_store.play_lottery(user_id, body.lottery)
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out.get("reason", "play failed"))
    return {**out, "wallet": wallet_store.get_wallet(user_id)}
