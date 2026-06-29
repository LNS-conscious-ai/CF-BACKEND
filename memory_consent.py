#!/usr/bin/env python3
"""
memory_consent.py
=================
Memory consent architecture for LNS Conscious AI Platform.

Users must explicitly grant consent before CF stores conversational memory.
Three levels are supported:

* ``none``  – No memory is stored.  Conversations are ephemeral.
* ``soft``  – Episodic memory (recent conversation context, not long-term profiling).
* ``deep``  – Long-term memory including patterns, preferences, and growth tracking.

Consent is granted AFTER the first CF reply (per LNS design lock) and can be
revoked at any time.  Revocation triggers immediate deletion of stored memory.

In-memory stores are used here; replace with PostgreSQL / Supabase in production.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Consent levels
# ---------------------------------------------------------------------------

CONSENT_LEVELS = ["none", "soft", "deep"]


# ---------------------------------------------------------------------------
# In-memory consent store (replace with DB table in production)
# ---------------------------------------------------------------------------

# user_id -> {"level": str, "granted_at": float, "revoked_at": Optional[float], "source": str}
_consent_store: Dict[str, Dict[str, Any]] = {}

# user_id -> bool tracking whether first CF reply has occurred
_first_reply_given: Dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Dataclass: ConsentStatus
# ---------------------------------------------------------------------------

@dataclass
class ConsentStatus:
    """Serializable representation of a user's memory consent state."""
    user_id: str
    level: str = "none"
    has_consent: bool = False
    granted_at: Optional[float] = None
    revoked_at: Optional[float] = None
    can_grant: bool = False   # True after first CF reply
    source: str = "explicit"  # explicit | implicit | admin
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def has_consent(user_id: str, minimum_level: str = "soft") -> bool:
    """
    Return True if the user has granted at least ``minimum_level`` consent.

    Parameters
    ----------
    user_id: str
        The authenticated user identifier.
    minimum_level: str
        One of "none", "soft", "deep".  Defaults to "soft".
    """
    if minimum_level == "none":
        return True  # No consent required for ephemeral mode

    rec = _consent_store.get(user_id)
    if not rec:
        return False

    if rec.get("revoked_at") is not None:
        return False

    level = rec.get("level", "none")
    level_index = CONSENT_LEVELS.index(level)
    minimum_index = CONSENT_LEVELS.index(minimum_level)
    return level_index >= minimum_index


def grant_consent(user_id: str, level: str, source: str = "explicit") -> ConsentStatus:
    """
    Grant memory consent at the specified level.

    Raises
    ------
    HTTPException(400)
        If the level is invalid or consent is granted before first CF reply.
    """
    if level not in CONSENT_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid consent level '{level}'. Valid: {CONSENT_LEVELS}",
        )

    # LNS design lock: consent only after first CF reply
    if not _first_reply_given.get(user_id, False):
        raise HTTPException(
            status_code=400,
            detail="Consent can only be granted after CF's first reply.",
        )

    _consent_store[user_id] = {
        "level": level,
        "granted_at": time.time(),
        "revoked_at": None,
        "source": source,
    }
    logger.info("Consent granted: user=%s level=%s source=%s", user_id, level, source)
    return get_consent_status(user_id)


def revoke_consent(user_id: str) -> ConsentStatus:
    """
    Revoke memory consent and trigger deletion of stored memory.

    Returns the updated status with ``revoked_at`` set.  The caller is
    responsible for physically deleting vectors / DB rows after this call.
    """
    rec = _consent_store.get(user_id)
    if not rec:
        logger.info("Revoke consent called for user with no existing consent: %s", user_id)
        # Still record a revocation tombstone for audit
        _consent_store[user_id] = {
            "level": "none",
            "granted_at": None,
            "revoked_at": time.time(),
            "source": "explicit",
        }
        return get_consent_status(user_id)

    rec["revoked_at"] = time.time()
    rec["level"] = "none"
    logger.info("Consent revoked: user=%s", user_id)

    # TODO: In production, trigger async deletion of all memory vectors
    # for this user from the vector store and conversation history table.
    _delete_user_memory(user_id)

    return get_consent_status(user_id)


def get_consent_status(user_id: str) -> ConsentStatus:
    """Return the full consent status for a user."""
    rec = _consent_store.get(user_id)
    if not rec:
        return ConsentStatus(
            user_id=user_id,
            level="none",
            has_consent=False,
            can_grant=_first_reply_given.get(user_id, False),
        )

    return ConsentStatus(
        user_id=user_id,
        level=rec["level"],
        has_consent=rec.get("revoked_at") is None and rec["level"] != "none",
        granted_at=rec.get("granted_at"),
        revoked_at=rec.get("revoked_at"),
        can_grant=_first_reply_given.get(user_id, False),
        source=rec.get("source", "explicit"),
    )


# ---------------------------------------------------------------------------
# First-reply gate (LNS design lock)
# ---------------------------------------------------------------------------

def mark_first_reply_given(user_id: str) -> None:
    """
    Call this after CF sends its first reply to the user.
    Unlocks the ability to grant consent.
    """
    _first_reply_given[user_id] = True
    logger.debug("First reply marked for user %s", user_id)


def can_grant_consent(user_id: str) -> bool:
    """Return True if the user has received their first CF reply."""
    return _first_reply_given.get(user_id, False)


# ---------------------------------------------------------------------------
# Memory deletion helper (stub for production integration)
# ---------------------------------------------------------------------------

def _delete_user_memory(user_id: str) -> None:
    """
    Stub: delete all stored memory for a user.

    In production this should:
    1. Delete vectors from ChromaDB (or your vector store) by user_id filter.
    2. Delete conversation history rows from Supabase / PostgreSQL.
    3. Delete any cached preference / profile JSON.
    4. Log the deletion for audit / DPDP / GDPR compliance.
    """
    logger.warning("MEMORY DELETION triggered for user %s — implement physical deletion", user_id)
    # Example (commented) for ChromaDB:
    # from chromadb import PersistentClient
    # client = PersistentClient(path="/path/to/chroma")
    # for collection in client.list_collections():
    #     collection.delete(where={"user_id": user_id})


# ---------------------------------------------------------------------------
# FastAPI integration helpers
# ---------------------------------------------------------------------------

def require_consent(minimum_level: str = "soft"):
    """
    Return a FastAPI dependency that enforces consent for an endpoint.

    Usage
    -----
    .. code-block:: python

        @app.post("/chat/store-memory")
        async def store_memory(
            request: Request,
            _: None = Depends(require_consent("soft")),
        ):
            ...
    """
    def _dependency(request: Request) -> None:
        user_id: str = request.headers.get("x-user-id", "anonymous")
        if not has_consent(user_id, minimum_level=minimum_level):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Memory consent required at level '{minimum_level}' or higher. "
                    "Please grant consent via /consent/grant first."
                ),
            )
    return _dependency


# ---------------------------------------------------------------------------
# Admin / debugging helpers
# ---------------------------------------------------------------------------

def list_all_consent() -> Dict[str, Any]:
    """Return a serialisable view of all consent records (protect with auth)."""
    return {
        uid: {
            "level": rec["level"],
            "granted_at": rec.get("granted_at"),
            "revoked_at": rec.get("revoked_at"),
            "source": rec.get("source"),
        }
        for uid, rec in _consent_store.items()
    }


def reset_consent_store() -> None:
    """Clear all consent state (testing only)."""
    global _consent_store, _first_reply_given
    _consent_store = {}
    _first_reply_given = {}
    logger.warning("Consent store reset")
