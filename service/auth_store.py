"""Credential store — the ID/password chain of the platform.

Provisioning hierarchy (who creates whom):
  * the **admin** account is built in;
  * the admin registers a **client** company → the client's login ID + password are generated
    at registration and handed over once;
  * a **client** creates its **employees** (representatives) → each rep gets generated
    credentials the same way;
  * **customers** use the demo phone-identity sign-in (no password in the demo).

Passwords are salted-hashed (sha256) — demo-grade, but the flow (issue → hand over once →
verify on login) mirrors production; swap the hash for bcrypt and this file doesn't change
shape.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from service import chat_store
from service.chat_store import _LOCK, _conn

_INIT_PATH: str | None = None

ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "admin123"  # demo bootstrap credential (shown on the login page)


def init() -> None:
    global _INIT_PATH
    if _INIT_PATH == chat_store.DB_PATH:
        return
    chat_store.init()
    with _LOCK, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS credentials (
                login_id   TEXT PRIMARY KEY,
                role       TEXT NOT NULL,       -- admin | client | rep
                entity_id  TEXT NOT NULL,       -- company_id for clients, rep_id for reps
                salt       TEXT NOT NULL,
                pw_hash    TEXT NOT NULL,
                created_by TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cred_entity ON credentials(entity_id);
            """
        )
    _INIT_PATH = chat_store.DB_PATH
    ensure_credential("admin", "admin", ADMIN_LOGIN, ADMIN_PASSWORD, created_by="bootstrap")


def _hash(salt: str, password: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def slugify(name: str) -> str:
    return "".join(ch for ch in name.lower().split(" ")[0] if ch.isalnum()) or "client"


def generate_password() -> str:
    return secrets.token_urlsafe(6)


def ensure_credential(role: str, entity_id: str, login_id: str, password: str,
                      created_by: str | None = None) -> dict[str, Any]:
    """Create the credential if the login is free; existing logins are left untouched."""
    init()
    with _LOCK, _conn() as c:
        if c.execute("SELECT 1 FROM credentials WHERE login_id=?", (login_id,)).fetchone():
            return {"login_id": login_id, "created": False}
        salt = secrets.token_hex(8)
        c.execute("INSERT INTO credentials (login_id, role, entity_id, salt, pw_hash, created_by, created_at) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (login_id, role, entity_id, salt, _hash(salt, password), created_by, chat_store._now()))
    return {"login_id": login_id, "created": True, "password": password}


def issue_credential(role: str, entity_id: str, base_login: str, created_by: str) -> dict[str, Any]:
    """Issue a fresh credential with a generated password; suffixes the login if taken."""
    init()
    login = base_login
    for i in range(2, 50):
        pw = generate_password()
        out = ensure_credential(role, entity_id, login, pw, created_by=created_by)
        if out.get("created"):
            return {"login_id": login, "password": pw}
        login = f"{base_login}{i}"
    raise RuntimeError("could not allocate a login id")


def verify(login_id: str, password: str) -> dict[str, Any] | None:
    """Return {role, entity_id, login_id} on a correct password, else None."""
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM credentials WHERE login_id=?", (login_id.strip(),)).fetchone()
    if not r or _hash(r["salt"], password) != r["pw_hash"]:
        return None
    return {"login_id": r["login_id"], "role": r["role"], "entity_id": r["entity_id"]}


def logins_for_entity(entity_id: str) -> list[str]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT login_id FROM credentials WHERE entity_id=?", (entity_id,)).fetchall()
    return [r["login_id"] for r in rows]
