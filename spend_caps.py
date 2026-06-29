#!/usr/bin/env python3
"""
spend_caps.py
==============
Spend cap and rate limiting module for LNS Conscious AI Platform.

Provides per-user and global spend tracking, rate limiting, and voice
usage caps for the CF (Conscious Friend) backend.  All state is held
in-memory (dict-based) with TTL-style reset windows.  For production,
swap the dictionaries out for Redis or a persistent cache.

Design notes
------------
* Daily windows reset at 00:00 UTC.  Hourly windows reset on the hour.
* Costs are approximate USD based on DeepInfra / Gemma 4 token pricing.
* A single ``check_and_record_usage`` dependency wraps every chat request.
* ``record_usage_*`` helpers are called after the response is generated.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, Optional

from fastapi import HTTPException, Request, Depends
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration – override via environment variables for flexibility
# ---------------------------------------------------------------------------

PER_USER_DAILY_SPEND_USD: float = float(os.environ.get("LNS_PER_USER_DAILY_SPEND", "5.0"))
PER_USER_PER_REQUEST_TOKEN_CAP: int = int(os.environ.get("LNS_PER_REQUEST_TOKEN_CAP", "8000"))
PER_USER_HOURLY_MESSAGE_LIMIT: int = int(os.environ.get("LNS_HOURLY_MESSAGE_LIMIT", "60"))
GLOBAL_DAILY_SPEND_USD: float = float(os.environ.get("LNS_GLOBAL_DAILY_SPEND", "100.0"))
VOICE_TTS_CHAR_CAP: int = int(os.environ.get("LNS_VOICE_TTS_CHAR_CAP", "300"))
VOICE_STT_SEC_CAP: int = int(os.environ.get("LNS_VOICE_STT_SEC_CAP", "60"))
AGENT_LOOP_MAX: int = int(os.environ.get("LNS_AGENT_LOOP_MAX", "5"))

# Approximate cost per 1K tokens (input + output averaged) — tune for your model
COST_PER_1K_TOKENS_USD: float = float(os.environ.get("LNS_COST_PER_1K_TOKENS", "0.003"))

# ---------------------------------------------------------------------------
# In-memory stores (replace with Redis in production)
# ---------------------------------------------------------------------------

# user_id -> {"daily_spend": float, "hourly_count": int, "last_reset_day": int, "last_reset_hour": int}
_user_spend: Dict[str, Dict[str, Any]] = {}

# Global daily spend tracking
_global_daily_spend: float = 0.0
_global_last_reset_day: int = 0

# Track voice usage per user (lightweight, no cost attached for now)
_user_voice_usage: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Helper: UTC day / hour buckets
# ---------------------------------------------------------------------------

def _current_utc_day() -> int:
    """Return the current UTC day as an integer (days since epoch)."""
    return int(time.time() // 86400)


def _current_utc_hour() -> int:
    """Return the current UTC hour bucket (hours since epoch)."""
    return int(time.time() // 3600)


# ---------------------------------------------------------------------------
# Dataclass: Usage snapshot (returned by helpers for downstream logging)
# ---------------------------------------------------------------------------

@dataclass
class UsageSnapshot:
    """Immutable snapshot of a user's current usage counters."""
    user_id: str
    daily_spend_usd: float = 0.0
    hourly_messages: int = 0
    tokens_this_request: int = 0
    global_daily_spend_usd: float = 0.0
    allowed: bool = True
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Internal store management
# ---------------------------------------------------------------------------

def _reset_user_window(user_id: str) -> None:
    """Reset daily / hourly counters if the UTC window has rolled over."""
    now_day = _current_utc_day()
    now_hour = _current_utc_hour()
    rec = _user_spend.setdefault(user_id, {
        "daily_spend": 0.0,
        "hourly_count": 0,
        "last_reset_day": now_day,
        "last_reset_hour": now_hour,
    })
    if rec["last_reset_day"] != now_day:
        rec["daily_spend"] = 0.0
        rec["last_reset_day"] = now_day
    if rec["last_reset_hour"] != now_hour:
        rec["hourly_count"] = 0
        rec["last_reset_hour"] = now_hour


def _reset_global_window() -> None:
    """Reset global daily spend if the UTC day has rolled over."""
    global _global_daily_spend, _global_last_reset_day
    now_day = _current_utc_day()
    if _global_last_reset_day != now_day:
        _global_daily_spend = 0.0
        _global_last_reset_day = now_day


# ---------------------------------------------------------------------------
# Core spend / rate-limit check
# ---------------------------------------------------------------------------

def check_user_limits(user_id: str, estimated_tokens: int = 0) -> UsageSnapshot:
    """
    Check whether a user is within their spend and rate limits.

    Parameters
    ----------
    user_id: str
        The authenticated user identifier.
    estimated_tokens: int
        Expected tokens for the incoming request (0 if unknown).

    Returns
    -------
    UsageSnapshot
        ``allowed`` is False if any cap is breached; ``reason`` explains why.
    """
    _reset_user_window(user_id)
    _reset_global_window()

    rec = _user_spend[user_id]
    snap = UsageSnapshot(user_id=user_id)
    snap.daily_spend_usd = rec["daily_spend"]
    snap.hourly_messages = rec["hourly_count"]
    snap.tokens_this_request = estimated_tokens
    snap.global_daily_spend_usd = _global_daily_spend

    # 1. Per-user hourly message cap
    if rec["hourly_count"] >= PER_USER_HOURLY_MESSAGE_LIMIT:
        snap.allowed = False
        snap.reason = (
            f"Hourly message limit reached ({PER_USER_HOURLY_MESSAGE_LIMIT}/hour). "
            "Please try again in the next hour."
        )
        logger.warning("Spend cap: user %s hit hourly message limit", user_id)
        return snap

    # 2. Per-request token cap
    if estimated_tokens > PER_USER_PER_REQUEST_TOKEN_CAP:
        snap.allowed = False
        snap.reason = (
            f"Request too large ({estimated_tokens} tokens). "
            f"Max allowed: {PER_USER_PER_REQUEST_TOKEN_CAP} tokens."
        )
        logger.warning("Spend cap: user %s exceeded token cap", user_id)
        return snap

    # 3. Per-user daily spend cap
    estimated_cost = (estimated_tokens / 1000) * COST_PER_1K_TOKENS_USD
    if rec["daily_spend"] + estimated_cost > PER_USER_DAILY_SPEND_USD:
        snap.allowed = False
        snap.reason = (
            f"Daily spend limit reached (${PER_USER_DAILY_SPEND_USD:.2f} USD). "
            "Please try again tomorrow."
        )
        logger.warning("Spend cap: user %s hit daily spend limit", user_id)
        return snap

    # 4. Global daily spend cap (hard stop)
    if _global_daily_spend + estimated_cost > GLOBAL_DAILY_SPEND_USD:
        snap.allowed = False
        snap.reason = (
            "Platform daily spend limit reached. Please try again later."
        )
        logger.warning("Spend cap: global daily spend limit reached")
        return snap

    return snap


# ---------------------------------------------------------------------------
# Usage recording helpers (call after the LLM response is generated)
# ---------------------------------------------------------------------------

def record_usage_tokens(user_id: str, input_tokens: int, output_tokens: int) -> None:
    """
    Record token usage after a request completes.

    Updates both per-user and global daily spend counters.
    """
    _reset_user_window(user_id)
    _reset_global_window()

    total_tokens = input_tokens + output_tokens
    cost = (total_tokens / 1000) * COST_PER_1K_TOKENS_USD

    rec = _user_spend[user_id]
    rec["daily_spend"] += cost
    rec["hourly_count"] += 1

    global _global_daily_spend
    _global_daily_spend += cost

    logger.info(
        "Usage recorded: user=%s tokens=%d cost=$%.6f daily_spend=$%.4f hourly=%d",
        user_id, total_tokens, cost, rec["daily_spend"], rec["hourly_count"],
    )


def record_usage_message(user_id: str) -> None:
    """
    Increment the hourly message counter for a non-LLM interaction
    (e.g. a system ping, voice metadata, or cached response).
    """
    _reset_user_window(user_id)
    rec = _user_spend[user_id]
    rec["hourly_count"] += 1


def record_voice_tts(user_id: str, char_count: int) -> None:
    """
    Validate and record a TTS request.
    Raises HTTPException if the character cap is exceeded.
    """
    if char_count > VOICE_TTS_CHAR_CAP:
        logger.warning("Voice TTS cap: user=%s chars=%d > cap=%d", user_id, char_count, VOICE_TTS_CHAR_CAP)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Voice response too long ({char_count} chars). "
                f"TTS limit: {VOICE_TTS_CHAR_CAP} characters per request."
            ),
        )
    _user_voice_usage.setdefault(user_id, {"tts_chars": 0, "stt_seconds": 0})
    _user_voice_usage[user_id]["tts_chars"] += char_count
    logger.info("Voice TTS recorded: user=%s chars=%d", user_id, char_count)


def record_voice_stt(user_id: str, duration_seconds: float) -> None:
    """
    Validate and record an STT request.
    Raises HTTPException if the duration cap is exceeded.
    """
    if duration_seconds > VOICE_STT_SEC_CAP:
        logger.warning("Voice STT cap: user=%s duration=%.1fs > cap=%ds", user_id, duration_seconds, VOICE_STT_SEC_CAP)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Recording too long ({duration_seconds:.1f}s). "
                f"STT limit: {VOICE_STT_SEC_CAP} seconds per recording."
            ),
        )
    _user_voice_usage.setdefault(user_id, {"tts_chars": 0, "stt_seconds": 0})
    _user_voice_usage[user_id]["stt_seconds"] += duration_seconds
    logger.info("Voice STT recorded: user=%s duration=%.1fs", user_id, duration_seconds)


def record_agent_loop(iteration: int) -> None:
    """
    Guardrail for CF Computer / agent loops.
    Raises HTTPException if the loop exceeds the configured maximum.
    """
    if iteration > AGENT_LOOP_MAX:
        logger.warning("Agent loop exceeded: iteration=%d > max=%d", iteration, AGENT_LOOP_MAX)
        raise HTTPException(
            status_code=429,
            detail=f"Agent loop limit ({AGENT_LOOP_MAX}) exceeded. Halting to prevent runaway execution.",
        )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def spend_cap_dependency(request: Request) -> UsageSnapshot:
    """
    FastAPI dependency to enforce spend / rate limits on every request.

    Usage
    -----
    .. code-block:: python

        @app.post("/chat/stream")
        async def chat_stream(
            body: ChatRequest,
            usage: UsageSnapshot = Depends(spend_cap_dependency),
        ):
            ...
    """
    # In production, extract from JWT / session / header
    user_id: str = request.headers.get("x-user-id", "anonymous")
    if not user_id or user_id == "anonymous":
        # Optionally reject anonymous users; here we allow but log loudly
        logger.warning("Spend cap check for anonymous user — consider requiring auth")

    # Optionally read estimated tokens from request body if available
    estimated_tokens = 0
    try:
        body = await request.json()
        estimated_tokens = body.get("estimated_tokens", 0) or body.get("max_tokens", 0)
    except Exception:
        pass

    snap = check_user_limits(user_id, estimated_tokens=estimated_tokens)
    if not snap.allowed:
        raise HTTPException(status_code=429, detail=snap.reason)

    # Attach snapshot to request state so downstream handlers can log it
    request.state.usage_snapshot = snap
    return snap


# ---------------------------------------------------------------------------
# User status endpoint helper
# ---------------------------------------------------------------------------

def get_user_status(user_id: str) -> Dict[str, Any]:
    """
    Return a serialisable dictionary of the user's current spend / limit state.
    Suitable for ``GET /user/status`` responses.
    """
    _reset_user_window(user_id)
    _reset_global_window()

    rec = _user_spend.get(user_id, {
        "daily_spend": 0.0,
        "hourly_count": 0,
        "last_reset_day": _current_utc_day(),
        "last_reset_hour": _current_utc_hour(),
    })
    voice = _user_voice_usage.get(user_id, {"tts_chars": 0, "stt_seconds": 0})

    return {
        "user_id": user_id,
        "daily_spend_usd": round(rec["daily_spend"], 4),
        "daily_spend_limit_usd": PER_USER_DAILY_SPEND_USD,
        "daily_spend_remaining_usd": round(max(0.0, PER_USER_DAILY_SPEND_USD - rec["daily_spend"]), 4),
        "hourly_messages": rec["hourly_count"],
        "hourly_message_limit": PER_USER_HOURLY_MESSAGE_LIMIT,
        "hourly_messages_remaining": max(0, PER_USER_HOURLY_MESSAGE_LIMIT - rec["hourly_count"]),
        "per_request_token_cap": PER_USER_PER_REQUEST_TOKEN_CAP,
        "voice_tts_chars_used": voice["tts_chars"],
        "voice_tts_char_cap": VOICE_TTS_CHAR_CAP,
        "voice_stt_seconds_used": round(voice["stt_seconds"], 2),
        "voice_stt_sec_cap": VOICE_STT_SEC_CAP,
        "agent_loop_max": AGENT_LOOP_MAX,
        "global_daily_spend_usd": round(_global_daily_spend, 4),
        "global_daily_spend_limit_usd": GLOBAL_DAILY_SPEND_USD,
        "global_daily_spend_remaining_usd": round(max(0.0, GLOBAL_DAILY_SPEND_USD - _global_daily_spend), 4),
    }


# ---------------------------------------------------------------------------
# Admin / debugging helpers (optional, protect with auth in production)
# ---------------------------------------------------------------------------

def reset_all_usage() -> None:
    """Clear all in-memory usage counters.  Useful for testing."""
    global _user_spend, _global_daily_spend, _user_voice_usage
    _user_spend = {}
    _global_daily_spend = 0.0
    _user_voice_usage = {}
    logger.warning("All usage counters reset")


def get_global_status() -> Dict[str, Any]:
    """Return global platform spend status."""
    _reset_global_window()
    return {
        "global_daily_spend_usd": round(_global_daily_spend, 4),
        "global_daily_spend_limit_usd": GLOBAL_DAILY_SPEND_USD,
        "global_daily_spend_remaining_usd": round(max(0.0, GLOBAL_DAILY_SPEND_USD - _global_daily_spend), 4),
    }
