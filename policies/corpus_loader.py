"""Parse the policy corpus into metadata-tagged chunks.

Corpus files live in ``policies/corpus/*.md`` and may contain multiple policy
documents separated by a line containing only ``===``. Each document begins with a
YAML front-matter block delimited by ``---`` and is followed by free-text paragraphs.
Each blank-line-delimited paragraph becomes one chunk (DR-RAG-1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CORPUS_DIR = Path(__file__).parent / "corpus"


@dataclass
class Chunk:
    id: str
    policy_id: str
    chunk_text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _normalise_meta(meta: dict[str, Any]) -> dict[str, Any]:
    pm = meta.get("payment_mode")
    if isinstance(pm, str) and pm.strip().lower() in {"", "any", "both", "*"}:
        pm = None
    issue = meta.get("issue_type", [])
    if isinstance(issue, str):
        issue = [s.strip() for s in issue.split(",") if s.strip()]
    return {
        "policy_id": meta["policy_id"],
        "category": meta.get("category", "*"),
        "payment_mode": pm,
        "issue_type": issue,
        "rule_type": meta.get("rule_type"),
        "title": meta.get("title", meta["policy_id"]),
    }


def _split_front_matter(doc: str) -> tuple[dict[str, Any], str]:
    doc = doc.strip()
    if not doc.startswith("---"):
        raise ValueError("policy document missing front-matter")
    _, fm, body = doc.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    return meta, body.strip()


def _chunk_body(body: str) -> list[str]:
    parts = [p.strip() for p in body.split("\n\n")]
    return [p for p in parts if len(p) >= 20]


def load_chunks(corpus_dir: Path | None = None) -> list[Chunk]:
    corpus_dir = corpus_dir or CORPUS_DIR
    chunks: list[Chunk] = []
    # A policy_id may appear in more than one corpus file; keep a running per-policy
    # counter so chunk ids stay globally unique (avoids embedding-map collisions).
    seq: dict[str, int] = {}
    for path in sorted(corpus_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for raw_doc in text.split("\n===\n"):
            if not raw_doc.strip():
                continue
            meta, body = _split_front_matter(raw_doc)
            meta = _normalise_meta(meta)
            pid = meta["policy_id"]
            for para in _chunk_body(body):
                i = seq.get(pid, 0)
                seq[pid] = i + 1
                chunks.append(
                    Chunk(id=f"{pid}#{i}", policy_id=pid, chunk_text=para, metadata=meta)
                )
    if not chunks:
        raise RuntimeError(f"no policy chunks found under {corpus_dir}")
    return chunks
