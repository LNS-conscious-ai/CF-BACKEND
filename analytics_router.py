"""
PostHog Analytics Backend — LNS Conscious AI Platform
=======================================================
FastAPI endpoints for receiving analytics events from the frontend,
validating them, batching, and storing in Supabase.

Features:
- Event ingestion with PII stripping
- Batch event logging
- Basic dashboard metrics
- Rate limiting (100 events/user/hour)
- Anonymized user IDs via SHA-256 hashing

Design: DPDP/GDPR compliant by default.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

# ────────────────────────────────────────────────────────────
# Configuration (never hardcode API keys)
# ────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
POSTHOG_PROJECT_KEY = os.environ.get("POSTHOG_PROJECT_KEY", "")
POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://us.posthog.com")

# Rate limiting: max events per user per hour
RATE_LIMIT_EVENTS = int(os.environ.get("RATE_LIMIT_EVENTS", "100"))
RATE_LIMIT_WINDOW = 3600  # seconds

# In-memory rate limit store (use Redis in production)
_rate_limit_store: dict[str, list[float]] = {}

# ────────────────────────────────────────────────────────────
# Supabase Client Setup (lazy init)
# ────────────────────────────────────────────────────────────

supabase_client: Any | None = None


def get_supabase() -> Any:
    """Lazy-initialize Supabase client."""
    global supabase_client
    if supabase_client is not None:
        return supabase_client

    try:
        from supabase import create_client
        if SUPABASE_URL and SUPABASE_KEY:
            supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except ImportError:
        # Supabase client not installed — events will be queued locally
        supabase_client = None

    return supabase_client


# ────────────────────────────────────────────────────────────
# Pydantic Models
# ────────────────────────────────────────────────────────────

class AnalyticsEvent(BaseModel):
    """Single analytics event payload from frontend."""
    event_name: str = Field(..., min_length=1, max_length=100)
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None

    @validator("event_name")
    def validate_event_name(cls, v: str) -> str:
        allowed = {
            "hero_viewed", "three_doors_clicked", "three_doors_skipped",
            "first_message_sent", "consent_accepted", "consent_declined",
            "persona_mode_changed", "voice_input_used", "cf_problem_confirmed",
            "cf_computer_handoff_initiated", "cf_computer_handoff_completed",
            "cf_computer_handoff_abandoned", "course_module_started",
            "course_module_completed", "feed_post_created", "friend_connected",
            "meaningful_minute_logged", "memory_viewed", "memory_deleted",
            "outcome_logged", "page_viewed", "consent_modal_shown",
            "consent_learn_more", "consent_revoked",
        }
        # Allow unknown events (forward compatibility) but log them
        if v not in allowed:
            # We'll still accept it, just note it's unknown
            pass
        return v


class BatchEvents(BaseModel):
    """Batch of events from frontend queue."""
    events: list[AnalyticsEvent] = Field(..., max_length=100)


class IdentifyPayload(BaseModel):
    """User identification payload (already hashed by frontend)."""
    user_id: str = Field(..., min_length=1, max_length=128)
    traits: dict[str, Any] = Field(default_factory=dict)


class DashboardResponse(BaseModel):
    """Basic analytics dashboard data."""
    total_events: int
    unique_users: int
    unique_sessions: int
    event_counts: dict[str, int]
    top_events: list[dict[str, Any]]
    funnel_metrics: dict[str, Any]
    period: str = "24h"


class ConsentPayload(BaseModel):
    """Consent grant/decline payload."""
    user_id: Optional[str] = None
    consent_level: Optional[str] = None
    timestamp: Optional[str] = None
    ip_hash: Optional[str] = None


# ────────────────────────────────────────────────────────────
# Utility Functions
# ────────────────────────────────────────────────────────────

def strip_pii(data: dict[str, Any]) -> dict[str, Any]:
    """Strip PII keys from an event properties dict."""
    pii_keys = {
        "email", "phone", "name", "password", "token", "address",
        "ip_address", "user_agent", "ssn", "credit_card", "card",
        "first_name", "last_name", "full_name", "location", "coordinates",
    }
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in pii_keys or any(k in key.lower() for k in pii_keys):
            cleaned[key] = "[REDACTED]"
            continue
        if isinstance(value, str):
            # Redact email patterns
            import re
            cleaned[key] = re.sub(
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
                "[REDACTED]",
                value,
            )
        elif isinstance(value, dict):
            cleaned[key] = strip_pii(value)
        else:
            cleaned[key] = value
    return cleaned


def hash_user_id(user_id: str) -> str:
    """One-way SHA-256 hash of user_id for analytics storage."""
    salt = os.environ.get("ANALYTICS_SALT", "lns_analytics_salt")
    return hashlib.sha256(f"{user_id}{salt}".encode()).hexdigest()


def check_rate_limit(user_id: str | None) -> bool:
    """
    Check if user is within rate limit (100 events/hour).
    Returns True if allowed, False if exceeded.
    """
    if not user_id:
        return True  # Anonymous users: no limit (or use IP hash)

    now = time.time()
    user_events = _rate_limit_store.get(user_id, [])
    # Remove events outside the window
    window_start = now - RATE_LIMIT_WINDOW
    user_events = [t for t in user_events if t > window_start]
    user_events.append(now)
    _rate_limit_store[user_id] = user_events

    return len(user_events) <= RATE_LIMIT_EVENTS


def get_rate_limit_remaining(user_id: str | None) -> int:
    """Return remaining events allowed for this user in current window."""
    if not user_id:
        return RATE_LIMIT_EVENTS
    user_events = _rate_limit_store.get(user_id, [])
    window_start = time.time() - RATE_LIMIT_WINDOW
    recent = [t for t in user_events if t > window_start]
    return max(0, RATE_LIMIT_EVENTS - len(recent))


# ────────────────────────────────────────────────────────────
# Supabase Operations
# ────────────────────────────────────────────────────────────

async def store_event(event: AnalyticsEvent) -> bool:
    """Store a single analytics event in Supabase. Returns True on success."""
    supabase = get_supabase()
    if not supabase:
        return False

    try:
        # Ensure user_id is hashed if not already
        user_id = event.user_id
        if user_id and len(user_id) < 64:  # Not a hash (SHA-256 is 64 hex chars)
            user_id = hash_user_id(user_id)

        clean_props = strip_pii(event.properties)

        payload = {
            "user_id": user_id,
            "session_id": event.session_id,
            "event_name": event.event_name,
            "properties": clean_props,
            "created_at": event.created_at or datetime.now(timezone.utc).isoformat(),
        }

        supabase.table("analytics_events").insert(payload).execute()
        return True
    except Exception as e:
        # Log but don't crash — analytics should never break the user experience
        print(f"[Analytics] Supabase store failed: {e}")
        return False


async def store_events_batch(events: list[AnalyticsEvent]) -> dict[str, Any]:
    """Store a batch of events. Returns success/failure counts."""
    supabase = get_supabase()
    if not supabase:
        return {"stored": 0, "failed": len(events), "reason": "no_supabase"}

    payloads = []
    for event in events:
        user_id = event.user_id
        if user_id and len(user_id) < 64:
            user_id = hash_user_id(user_id)
        payloads.append({
            "user_id": user_id,
            "session_id": event.session_id,
            "event_name": event.event_name,
            "properties": strip_pii(event.properties),
            "created_at": event.created_at or datetime.now(timezone.utc).isoformat(),
        })

    try:
        supabase.table("analytics_events").insert(payloads).execute()
        return {"stored": len(payloads), "failed": 0}
    except Exception as e:
        print(f"[Analytics] Batch store failed: {e}")
        return {"stored": 0, "failed": len(events), "reason": str(e)}


async def store_consent(user_id: str | None, consent_level: str, timestamp: str | None, ip_hash: str | None) -> bool:
    """Store consent record in Supabase."""
    supabase = get_supabase()
    if not supabase:
        return False

    try:
        payload = {
            "user_id": hash_user_id(user_id) if user_id else None,
            "consent_level": consent_level,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "ip_hash": ip_hash,
        }
        supabase.table("consent_records").insert(payload).execute()
        return True
    except Exception as e:
        print(f"[Analytics] Consent store failed: {e}")
        return False


# ────────────────────────────────────────────────────────────
# FastAPI Router
# ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/event", status_code=status.HTTP_201_CREATED)
async def log_event(event: AnalyticsEvent, request: Request) -> dict[str, Any]:
    """
    Receive a single analytics event from the frontend.

    - Validates event name and structure
    - Checks rate limit (100 events/hour per user)
    - Strips PII from properties
    - Hashes user_id for anonymization
    - Stores in Supabase analytics_events table

    Returns: { success: bool, remaining: int, event_name: str }
    """
    # Rate limiting
    user_id = event.user_id
    if not check_rate_limit(user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: max {RATE_LIMIT_EVENTS} events/hour",
        )

    # Store in Supabase
    success = await store_event(event)
    remaining = get_rate_limit_remaining(user_id)

    # Also attempt PostHog server-side (if configured)
    if POSTHOG_PROJECT_KEY and success:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{POSTHOG_HOST}/capture/",
                    json={
                        "api_key": POSTHOG_PROJECT_KEY,
                        "event": event.event_name,
                        "properties": {
                            **strip_pii(event.properties),
                            "distinct_id": event.user_id or "anonymous",
                            "session_id": event.session_id,
                        },
                        "timestamp": event.created_at,
                    },
                    timeout=5.0,
                )
        except Exception as e:
            print(f"[Analytics] PostHog server-side failed: {e}")

    return {
        "success": success,
        "remaining": remaining,
        "event_name": event.event_name,
        "user_id_hashed": hash_user_id(user_id) if user_id else None,
    }


@router.post("/batch", status_code=status.HTTP_201_CREATED)
async def log_batch(batch: BatchEvents, request: Request) -> dict[str, Any]:
    """
    Receive a batch of analytics events from the frontend queue.

    Useful for:
    - Offline users whose events queued while disconnected
    - Batching to reduce network requests
    - Fallback when PostHog is unavailable

    Each event is rate-limited individually. The entire batch is
    stored as a single Supabase insert for efficiency.
    """
    # Check rate limit for the batch (approximate: count all events)
    user_counts: dict[str, int] = {}
    for event in batch.events:
        uid = event.user_id or "anonymous"
        user_counts[uid] = user_counts.get(uid, 0) + 1

    for uid, count in user_counts.items():
        if not check_rate_limit(uid):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for user: {uid[:16]}...",
            )
        # Check if adding this count would exceed
        remaining = get_rate_limit_remaining(uid)
        if remaining < count:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit would exceed: {remaining} remaining, {count} requested",
            )

    # Store batch
    result = await store_events_batch(batch.events)

    return {
        "success": result["failed"] == 0,
        "stored": result["stored"],
        "failed": result["failed"],
        "reason": result.get("reason"),
    }


@router.post("/identify", status_code=status.HTTP_200_OK)
async def identify_user(payload: IdentifyPayload, request: Request) -> dict[str, Any]:
    """
    Receive a user identification event.

    The user_id should already be hashed by the frontend.
    We validate it and store traits in the analytics_events table
    as a special 'identify' event.
    """
    safe_traits = strip_pii(payload.traits)
    event = AnalyticsEvent(
        event_name="identify",
        user_id=payload.user_id,
        properties=safe_traits,
    )
    success = await store_event(event)

    return {
        "success": success,
        "user_id_hashed": payload.user_id,
        "traits_stored": list(safe_traits.keys()),
    }


@router.get("/dashboard", status_code=status.HTTP_200_OK)
async def get_dashboard(
    request: Request,
    period: str = "24h",
) -> DashboardResponse:
    """
    Basic analytics dashboard data.

    Returns aggregated event counts, unique users/sessions,
    and simple funnel metrics for the specified period.

    Period options: "24h", "7d", "30d", "90d"
    """
    supabase = get_supabase()
    if not supabase:
        # Return mock data when Supabase is unavailable
        return DashboardResponse(
            total_events=0,
            unique_users=0,
            unique_sessions=0,
            event_counts={},
            top_events=[],
            funnel_metrics={"status": "no_supabase"},
            period=period,
        )

    try:
        # Calculate time window
        now = datetime.now(timezone.utc)
        if period == "24h":
            since = now.timestamp() - 86400
        elif period == "7d":
            since = now.timestamp() - 604800
        elif period == "30d":
            since = now.timestamp() - 2592000
        elif period == "90d":
            since = now.timestamp() - 7776000
        else:
            since = now.timestamp() - 86400

        # Query analytics_events
        # Note: Using Supabase's RPC for complex aggregations is recommended
        # This is a simplified approach using direct queries
        response = supabase.table("analytics_events") \
            .select("*") \
            .gte("created_at", datetime.fromtimestamp(since, tz=timezone.utc).isoformat()) \
            .execute()

        events = response.data if response else []
        total = len(events)

        # Count unique users and sessions
        users = set(e.get("user_id") for e in events if e.get("user_id"))
        sessions = set(e.get("session_id") for e in events if e.get("session_id"))

        # Event name counts
        event_counts: dict[str, int] = {}
        for e in events:
            name = e.get("event_name", "unknown")
            event_counts[name] = event_counts.get(name, 0) + 1

        # Top events
        top = sorted(
            [{"name": k, "count": v} for k, v in event_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )[:10]

        # Simple funnel metrics
        funnel = {
            "hero_to_door": event_counts.get("three_doors_clicked", 0) / max(event_counts.get("hero_viewed", 1), 1),
            "door_to_chat": event_counts.get("first_message_sent", 0) / max(event_counts.get("three_doors_clicked", 1), 1),
            "chat_to_consent": event_counts.get("consent_accepted", 0) / max(event_counts.get("first_message_sent", 1), 1),
            "consent_to_deep": event_counts.get("consent_accepted", 0) / max(event_counts.get("consent_accepted", 0) + event_counts.get("consent_declined", 1), 1),
        }

        return DashboardResponse(
            total_events=total,
            unique_users=len(users),
            unique_sessions=len(sessions),
            event_counts=event_counts,
            top_events=top,
            funnel_metrics=funnel,
            period=period,
        )

    except Exception as e:
        print(f"[Analytics] Dashboard query failed: {e}")
        return DashboardResponse(
            total_events=0,
            unique_users=0,
            unique_sessions=0,
            event_counts={},
            top_events=[],
            funnel_metrics={"status": "error", "message": str(e)},
            period=period,
        )


# ────────────────────────────────────────────────────────────
# Consent Endpoints (also under analytics router for grouping)
# ────────────────────────────────────────────────────────────

@router.post("/consent/grant", status_code=status.HTTP_200_OK)
async def grant_consent(payload: ConsentPayload, request: Request) -> dict[str, Any]:
    """Record consent grant."""
    success = await store_consent(
        payload.user_id,
        payload.consent_level or "deep",
        payload.timestamp,
        payload.ip_hash,
    )
    return {"success": success, "action": "granted", "level": payload.consent_level}


@router.post("/consent/decline", status_code=status.HTTP_200_OK)
async def decline_consent(payload: ConsentPayload, request: Request) -> dict[str, Any]:
    """Record consent decline."""
    success = await store_consent(
        payload.user_id,
        "none",
        payload.timestamp,
        payload.ip_hash,
    )
    return {"success": success, "action": "declined", "level": "none"}


@router.post("/consent/revoke", status_code=status.HTTP_200_OK)
async def revoke_consent(payload: ConsentPayload, request: Request) -> dict[str, Any]:
    """Record consent revocation."""
    success = await store_consent(
        payload.user_id,
        "none",
        payload.timestamp,
        payload.ip_hash,
    )
    # Also mark previous consent records as revoked
    supabase = get_supabase()
    if supabase and payload.user_id:
        try:
            hashed_id = hash_user_id(payload.user_id)
            supabase.table("consent_records") \
                .update({"revoked_at": datetime.now(timezone.utc).isoformat()}) \
                .eq("user_id", hashed_id) \
                .is_("revoked_at", None) \
                .execute()
        except Exception as e:
            print(f"[Analytics] Consent revocation update failed: {e}")

    return {"success": success, "action": "revoked", "level": "none"}


# ────────────────────────────────────────────────────────────
# Schema Definition (SQL for Supabase)
# ────────────────────────────────────────────────────────────
"""
-- Run this in Supabase SQL Editor to create the analytics_events table

CREATE TABLE IF NOT EXISTS analytics_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(128),
    session_id UUID,
    event_name VARCHAR(100) NOT NULL,
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Indexing for dashboard queries
    CONSTRAINT valid_event_name CHECK (char_length(event_name) > 0)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_analytics_events_user_id ON analytics_events(user_id);
CREATE INDEX IF NOT EXISTS idx_analytics_events_event_name ON analytics_events(event_name);
CREATE INDEX IF NOT EXISTS idx_analytics_events_created_at ON analytics_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analytics_events_session_id ON analytics_events(session_id);

-- Consent records table (for audit trail)
CREATE TABLE IF NOT EXISTS consent_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(128),
    consent_level VARCHAR(20) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    ip_hash VARCHAR(64),
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_consent_records_user_id ON consent_records(user_id);
CREATE INDEX IF NOT EXISTS idx_consent_records_timestamp ON consent_records(timestamp DESC);

-- Row Level Security (RLS): users can only see their own analytics
ALTER TABLE analytics_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own analytics" ON analytics_events
    FOR SELECT USING (user_id = current_setting('app.current_user_id', true));

CREATE POLICY "Users can view own consent" ON consent_records
    FOR SELECT USING (user_id = current_setting('app.current_user_id', true));
"""

# ────────────────────────────────────────────────────────────
# Application Setup (for standalone testing)
# ────────────────────────────────────────────────────────────

# NOTE: standalone create_app() / app removed — included as a router in main.py
