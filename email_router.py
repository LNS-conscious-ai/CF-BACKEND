#!/usr/bin/env python3
"""
main_email_endpoints.py
=======================
FastAPI endpoints for LNS email automation and outcome data flywheel.

Provides:
- Waitlist management (join, invite, status check)
- Email automation (test send, webhook reception)
- Outcome logging (advice → action → outcome)
- Engagement analytics

Author: LNS Engineering
Version: 1.0.0
"""

import os
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, BackgroundTasks, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, validator

# ───────────────────────────────────────────────────────────────────────────────
# IMPORT EMAIL AUTOMATION ENGINE
# ───────────────────────────────────────────────────────────────────────────────

try:
    from email_automation import EmailAutomation, handle_waitlist_signup_event
except ImportError:
    # Allow standalone import during development
    EmailAutomation = None  # type: ignore
    handle_waitlist_signup_event = None  # type: ignore

# ───────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ───────────────────────────────────────────────────────────────────────────────

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("email_endpoints")

# ───────────────────────────────────────────────────────────────────────────────
# SUPABASE CLIENT INITIALIZATION (lazy)
# ───────────────────────────────────────────────────────────────────────────────

_supabase_client: Optional[Any] = None


def get_supabase_client() -> Any:
    """Get or create the Supabase client (service role)."""
    global _supabase_client
    if _supabase_client is None:
        try:
            from supabase import create_client, Client
            _supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        except ImportError:
            logger.error("supabase-py not installed. Database operations will fail.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database client not available",
            )
    return _supabase_client


# ───────────────────────────────────────────────────────────────────────────────
# FASTAPI APP
# ───────────────────────────────────────────────────────────────────────────────

router = APIRouter()

# ───────────────────────────────────────────────────────────────────────────────
# PYDANTIC MODELS
# ───────────────────────────────────────────────────────────────────────────────

class WaitlistJoinRequest(BaseModel):
    """Request body for joining the waitlist."""
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(default="student", pattern="^(student|university)$")
    university_name: Optional[str] = Field(default=None, max_length=255)
    cohort: str = Field(default="beta_1", max_length=50)

class WaitlistInviteRequest(BaseModel):
    """Admin request to invite a user from the waitlist to beta."""
    email: EmailStr
    invited_by: Optional[str] = None  # Admin user ID or email

class TestEmailRequest(BaseModel):
    """Request body for sending a test email."""
    email: EmailStr
    name: str = Field(default="Test User", max_length=255)
    email_type: str = Field(default="welcome", pattern="^(welcome|founder_note|cohort_confirm)$")
    cohort: str = Field(default="beta_1", max_length=50)

class OutcomeLogRequest(BaseModel):
    """Request body for logging an outcome (advice → action → outcome)."""
    session_id: str
    advice_given: str = Field(..., min_length=1)
    action_taken: Optional[str] = None
    outcome_observed: Optional[str] = None
    outcome_type: str = Field(default="unknown", pattern="^(successful|partial|failed|unknown)$")
    consent_given: bool = Field(default=False)
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

class WebhookPayload(BaseModel):
    """Generic webhook payload for email delivery status."""
    type: Optional[str] = None
    event: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    email_id: Optional[str] = None

# ───────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ───────────────────────────────────────────────────────────────────────────────

def _handle_db_error(e: Exception, operation: str) -> None:
    """Log and raise on database errors."""
    logger.error(f"Database error during {operation}: {e}")
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Database operation failed: {operation}",
    )

# ───────────────────────────────────────────────────────────────────────────────
# ENDPOINTS: WAITLIST
# ───────────────────────────────────────────────────────────────────────────────

@router.post("/waitlist/join", status_code=status.HTTP_201_CREATED)
async def waitlist_join(
    request: WaitlistJoinRequest,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """
    Add a new user to the waitlist and trigger the welcome email sequence.

    - Inserts into `waitlist_entries` table
    - Triggers 3-email welcome sequence (Day 0, Day 3, Day 7)
    - Returns the waitlist entry ID and sequence status
    """
    try:
        supabase = get_supabase_client()

        # Check if email already exists
        existing = (
            supabase.table("waitlist_entries")
            .select("id, status, email")
            .eq("email", request.email)
            .execute()
        )
        if existing.data and len(existing.data) > 0:
            return {
                "success": False,
                "message": "Email already on waitlist",
                "waitlist_id": existing.data[0]["id"],
                "status": existing.data[0]["status"],
            }

        # Insert new waitlist entry
        entry = {
            "email": request.email,
            "name": request.name,
            "type": request.type,
            "university_name": request.university_name,
            "cohort": request.cohort,
            "status": "pending",
            "welcome_sequence_status": "not_started",
        }
        result = supabase.table("waitlist_entries").insert(entry).execute()
        waitlist_id = result.data[0]["id"]

        # Trigger welcome sequence in background (non-blocking)
        if EmailAutomation is not None:
            automation = EmailAutomation(api_key=RESEND_API_KEY)
            sequence_result = await automation.trigger_welcome_sequence(
                user_email=request.email,
                user_name=request.name,
                waitlist_type=request.type,
                university_name=request.university_name,
                cohort=request.cohort,
            )

            # Update waitlist entry with sequence status
            supabase.table("waitlist_entries").update({
                "welcome_sequence_status": "email_1_sent",
            }).eq("id", waitlist_id).execute()

            return {
                "success": True,
                "message": "Welcome to the conscious builders. Your sequence is starting.",
                "waitlist_id": waitlist_id,
                "sequence": sequence_result,
            }
        else:
            logger.warning("EmailAutomation not available. Skipping welcome sequence.")
            return {
                "success": True,
                "message": "Added to waitlist. Email automation temporarily unavailable.",
                "waitlist_id": waitlist_id,
            }

    except HTTPException:
        raise
    except Exception as e:
        _handle_db_error(e, "waitlist join")
        return {"success": False, "message": "Failed to join waitlist"}  # fallback


@router.post("/waitlist/invite", status_code=status.HTTP_200_OK)
async def waitlist_invite(request: WaitlistInviteRequest) -> Dict[str, Any]:
    """
    Invite a user from the waitlist to the beta (admin only).

    Updates:
    - `status` → 'invited'
    - `welcome_sequence_status` → 'completed' (if not already)
    """
    try:
        supabase = get_supabase_client()

        # Update waitlist entry status
        result = (
            supabase.table("waitlist_entries")
            .update({
                "status": "invited",
                "updated_at": datetime.now().isoformat(),
            })
            .eq("email", request.email)
            .execute()
        )

        if not result.data or len(result.data) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email not found on waitlist",
            )

        # Optionally queue the cohort confirmation email immediately
        if EmailAutomation is not None:
            try:
                from email_automation import EmailUser
                entry = result.data[0]
                automation = EmailAutomation(api_key=RESEND_API_KEY)
                user = EmailUser(
                    email=entry["email"],
                    name=entry.get("name", "Builder"),
                    cohort=entry.get("cohort", "beta_1"),
                )
                await automation.send_email(user, "cohort_confirm")
            except Exception as _e:
                logger.warning(f"Cohort confirm email skipped: {_e}")

        return {
            "success": True,
            "message": f"Invitation sent to {request.email}",
            "waitlist_id": result.data[0]["id"],
            "status": "invited",
        }

    except HTTPException:
        raise
    except Exception as e:
        _handle_db_error(e, "waitlist invite")
        return {"success": False, "message": "Failed to invite user"}


@router.get("/waitlist/status/{email}")
async def waitlist_status(email: str) -> Dict[str, Any]:
    """
    Check the current waitlist status for a given email.

    Returns:
        - status: 'pending', 'invited', 'active', 'completed', 'unsubscribed'
        - welcome_sequence_status
        - cohort
    """
    try:
        supabase = get_supabase_client()
        result = (
            supabase.table("waitlist_entries")
            .select("id, email, name, status, welcome_sequence_status, cohort, created_at")
            .eq("email", email)
            .execute()
        )

        if not result.data or len(result.data) == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email not found on waitlist",
            )

        entry = result.data[0]
        return {
            "success": True,
            "waitlist_id": entry["id"],
            "email": entry["email"],
            "name": entry.get("name"),
            "status": entry["status"],
            "welcome_sequence_status": entry["welcome_sequence_status"],
            "cohort": entry["cohort"],
            "joined_at": entry["created_at"],
        }

    except HTTPException:
        raise
    except Exception as e:
        _handle_db_error(e, "waitlist status check")
        return {"success": False, "message": "Failed to check status"}


# ───────────────────────────────────────────────────────────────────────────────
# ENDPOINTS: EMAIL
# ───────────────────────────────────────────────────────────────────────────────

@router.post("/email/send-test", status_code=status.HTTP_200_OK)
async def email_send_test(request: TestEmailRequest) -> Dict[str, Any]:
    """
    Send a test email for development/testing purposes.

    Does NOT update waitlist status or trigger sequences.
    """
    if EmailAutomation is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email automation module not available",
        )

    try:
        from email_automation import EmailUser

        automation = EmailAutomation(api_key=RESEND_API_KEY)
        user = EmailUser(
            email=request.email,
            name=request.name,
            cohort=request.cohort,
        )

        delivery = await automation.send_email(user, request.email_type)

        return {
            "success": delivery.status in ("sent", "delivered"),
            "delivery_id": delivery.delivery_id,
            "status": delivery.status,
            "email_type": request.email_type,
            "recipient": request.email,
            "message": f"Test email '{request.email_type}' sent to {request.email}",
        }

    except Exception as e:
        logger.error(f"Test email failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send test email: {str(e)}",
        )


@router.post("/email/webhook", status_code=status.HTTP_200_OK)
async def email_webhook(request: Request) -> Dict[str, Any]:
    """
    Receive and process email delivery status webhooks from Resend or SendGrid.

    Supported events:
    - sent, delivered, opened, clicked, bounced, failed

    Updates the `email_deliveries` table with the latest status.
    """
    try:
        payload = await request.json()
        logger.info(f"Webhook received: {payload}")

        if EmailAutomation is not None:
            automation = EmailAutomation(api_key=RESEND_API_KEY)
            result = automation.handle_delivery_webhook(payload)

            # Optionally persist to Supabase
            if result.get("delivery_id"):
                try:
                    supabase = get_supabase_client()
                    supabase.table("email_deliveries").update({
                        "status": result["status"],
                        "updated_at": datetime.now().isoformat(),
                    }).eq("id", result["delivery_id"]).execute()
                except Exception as db_err:
                    logger.warning(f"Webhook DB update failed: {db_err}")

            return {"success": True, "processed": result}

        return {"success": True, "processed": payload, "note": "EmailAutomation not loaded"}

    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        # Always return 200 to email providers so they don't retry indefinitely
        return {"success": False, "error": str(e)}


# ───────────────────────────────────────────────────────────────────────────────
# ENDPOINTS: OUTCOME DATA FLYWHEEL
# ───────────────────────────────────────────────────────────────────────────────

@router.post("/outcome/log", status_code=status.HTTP_201_CREATED)
async def outcome_log(request: OutcomeLogRequest) -> Dict[str, Any]:
    """
    Log an outcome: advice → action → outcome.

    This is the core data flywheel endpoint. Every CF recommendation and
    user-reported result flows through here.

    Fields:
    - advice_given: What CF recommended
    - action_taken: What the user actually did
    - outcome_observed: What happened as a result
    - outcome_type: successful, partial, failed, unknown
    - consent_given: DPDP compliance flag
    """
    try:
        supabase = get_supabase_client()

        # In a real request, user_id would come from auth context (JWT)
        # For now, we accept it from metadata or use a placeholder for the schema
        user_id = request.metadata.get("user_id") if request.metadata else None
        if not user_id:
            # In production, extract from auth JWT: auth.uid()
            user_id = str(uuid.uuid4())  # Placeholder — replace with real auth

        row = {
            "session_id": request.session_id,
            "user_id": user_id,
            "advice_given": request.advice_given,
            "action_taken": request.action_taken,
            "outcome_observed": request.outcome_observed,
            "outcome_type": request.outcome_type,
            "consent_given": request.consent_given,
            "tags": request.tags or [],
            "metadata": request.metadata or {},
            "anonymized_session_id": uuid.uuid4(),  # Generate fresh anonymized ID
        }

        result = supabase.table("outcome_logs").insert(row).execute()
        outcome_id = result.data[0]["id"]

        return {
            "success": True,
            "outcome_id": outcome_id,
            "message": "Outcome logged successfully. Thank you for helping CF get better.",
        }

    except Exception as e:
        _handle_db_error(e, "outcome log")
        return {"success": False, "message": "Failed to log outcome"}


@router.get("/outcome/status/{user_id}")
async def outcome_status(user_id: str) -> Dict[str, Any]:
    """
    Get a user's outcome summary and status.

    Returns:
        - Total outcomes logged
        - Breakdown by outcome_type (successful, partial, failed, unknown)
        - Consent rate
    """
    try:
        supabase = get_supabase_client()

        # Use the SQL function get_user_outcome_stats
        result = supabase.rpc("get_user_outcome_stats", {"p_user_id": user_id}).execute()

        if result.data and len(result.data) > 0:
            stats = result.data[0]
            return {
                "success": True,
                "user_id": user_id,
                "stats": stats,
            }

        # Fallback: raw query if RPC not available
        raw = (
            supabase.table("outcome_logs")
            .select("outcome_type, consent_given")
            .eq("user_id", user_id)
            .execute()
        )

        outcomes = raw.data or []
        total = len(outcomes)
        successful = sum(1 for o in outcomes if o["outcome_type"] == "successful")
        partial = sum(1 for o in outcomes if o["outcome_type"] == "partial")
        failed = sum(1 for o in outcomes if o["outcome_type"] == "failed")
        unknown = sum(1 for o in outcomes if o["outcome_type"] == "unknown")
        consented = sum(1 for o in outcomes if o.get("consent_given"))

        consent_rate = round(consented * 100.0 / total, 2) if total > 0 else 0

        return {
            "success": True,
            "user_id": user_id,
            "stats": {
                "total_outcomes": total,
                "successful_count": successful,
                "partial_count": partial,
                "failed_count": failed,
                "unknown_count": unknown,
                "consent_rate": consent_rate,
            },
        }

    except Exception as e:
        _handle_db_error(e, "outcome status")
        return {"success": False, "message": "Failed to fetch outcome status"}


# ───────────────────────────────────────────────────────────────────────────────
# ENDPOINTS: ANALYTICS
# ───────────────────────────────────────────────────────────────────────────────

@router.get("/analytics/engagement/{user_id}")
async def analytics_engagement(user_id: str) -> Dict[str, Any]:
    """
    Get engagement metrics for a specific user.

    Returns:
        - total_conversations
        - total_messages_sent
        - total_voice_messages
        - total_reports_generated
        - first_session_at, last_session_at
        - total_session_time_minutes
        - meaningful_minutes_count (north star metric)
    """
    try:
        supabase = get_supabase_client()
        result = (
            supabase.table("engagement_metrics")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )

        if not result.data or len(result.data) == 0:
            return {
                "success": True,
                "user_id": user_id,
                "metrics": None,
                "message": "No engagement data found for this user.",
            }

        metrics = result.data[0]
        return {
            "success": True,
            "user_id": user_id,
            "metrics": {
                "total_conversations": metrics.get("total_conversations", 0),
                "total_messages_sent": metrics.get("total_messages_sent", 0),
                "total_voice_messages": metrics.get("total_voice_messages", 0),
                "total_reports_generated": metrics.get("total_reports_generated", 0),
                "first_session_at": metrics.get("first_session_at"),
                "last_session_at": metrics.get("last_session_at"),
                "total_session_time_minutes": metrics.get("total_session_time_minutes", 0),
                "meaningful_minutes_count": metrics.get("meaningful_minutes_count", 0),
            },
        }

    except Exception as e:
        _handle_db_error(e, "engagement analytics")
        return {"success": False, "message": "Failed to fetch engagement metrics"}


# ───────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ───────────────────────────────────────────────────────────────────────────────

@router.get("/email/health")
async def health_check() -> Dict[str, str]:
    """Simple health check endpoint."""
    return {"status": "ok", "service": "lns-email-outcome-api"}


# ───────────────────────────────────────────────────────────────────────────────
# ERROR HANDLERS
# ───────────────────────────────────────────────────────────────────────────────

# NOTE: standalone @app.exception_handler blocks removed — handled globally in main.py
