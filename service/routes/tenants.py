"""Multi-tenant policy endpoints: companies upload their own support policies.

A company (Zomato, Swiggy, …) registers, uploads its refund/replacement/guideline documents,
and from then on chat sessions bound to that company are answered **from that company's
policy**: each query is embedded, semantically searched against the company's chunks, and the
top paragraphs ground the agent's replies and escalation context (RAG per tenant).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from service import policy_store

router = APIRouter()


class CreateCompany(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)


class UploadPolicy(BaseModel):
    doc_name: str = Field(..., min_length=1, max_length=120)
    text: str = Field(..., min_length=40, max_length=400_000)


@router.get("/api/companies")
def list_companies() -> list[dict]:
    return policy_store.list_companies()


@router.post("/api/companies")
def create_company(body: CreateCompany) -> dict:
    return policy_store.create_company(body.name)


@router.get("/api/companies/{company_id}/policies")
def list_policies(company_id: str) -> list[dict]:
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    return policy_store.list_documents(company_id)


@router.post("/api/companies/{company_id}/policies")
def upload_policy(company_id: str, body: UploadPolicy) -> dict:
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    out = policy_store.upload_policy(company_id, body.doc_name, body.text)
    if out["chunks"] == 0:
        raise HTTPException(status_code=422, detail="document produced no usable paragraphs")
    return out


@router.get("/api/companies/{company_id}/search")
def search_policy(company_id: str, q: str) -> list[dict]:
    """Debug/ops endpoint: see exactly which paragraphs a query retrieves."""
    if not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")
    return policy_store.search(company_id, q)
