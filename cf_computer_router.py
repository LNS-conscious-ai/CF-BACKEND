"""
CF Computer FastAPI Endpoint
LNS Conscious AI Platform

POST /cf-computer/report — Generate a validation report PDF.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# ── CF Computer imports ──────────────────────────────────────
from cf_computer import CFComputer, CFComputerError, ReportRateLimiter, StepExecutionError
from pdf_generator import PDFGenerator


# ── Supabase client (optional, graceful if missing) ──────────
try:
    from supabase import create_client, Client
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
    supabase: Client | None = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None
except Exception:
    supabase = None


# ── Pydantic Models ──────────────────────────────────────────

class ReportRequest(BaseModel):
    """Request body for CF Computer report generation."""

    problem_statement: str = Field(..., min_length=10, max_length=2000,
                                   description="The confirmed problem from CF conversation.")
    session_id: str = Field(..., min_length=1, description="Conversation session UUID.")
    user_id: str = Field(..., min_length=1, description="User UUID.")


class ReportResponse(BaseModel):
    """Response body for successful report generation."""

    status: str
    report_id: str
    download_url: str
    steps_completed: list[dict[str, Any]]
    conscious_review: str
    total_cost_usd: float
    total_tokens: int
    generated_at: str
    v1_disclaimer: str


class ReportErrorResponse(BaseModel):
    """Response body for errors."""

    status: str = "error"
    error_code: str
    message: str
    details: dict[str, Any] | None = None


class ProgressEvent(BaseModel):
    """SSE progress event."""

    step_number: int
    step_name: str
    status: str  # started | completed | failed


# ── FastAPI App ──────────────────────────────────────────────

router = APIRouter()

# Ensure reports output dir exists at import time
os.makedirs(os.environ.get("LNS_REPORTS_DIR", "/tmp/lns_reports"), exist_ok=True)

# Shared engine instances (singleton pattern)
_computer: CFComputer | None = None
_pdf_generator: PDFGenerator | None = None


def _get_computer() -> CFComputer:
    """Lazy-initialize CF Computer engine."""
    global _computer
    if _computer is None:
        _computer = CFComputer()
    return _computer


def _get_pdf_generator() -> PDFGenerator:
    """Lazy-initialize PDF generator."""
    global _pdf_generator
    if _pdf_generator is None:
        output_dir = os.environ.get("LNS_REPORTS_DIR", "/tmp/lns_reports")
        _pdf_generator = PDFGenerator(output_dir=output_dir)
    return _pdf_generator


# ── Spend Caps Helper ────────────────────────────────────────

def _check_spend_caps(user_id: str) -> tuple[bool, str]:
    """
    Check if user is within spend caps.

    In production, this queries a spend_caps table or service.
    For v1, we check report_usage as a proxy.

    Returns:
        (allowed: bool, reason: str)
    """
    if supabase is None:
        # No Supabase = allow (local dev mode)
        return True, ""

    try:
        today = date.today().isoformat()
        resp = supabase.table("report_usage") \
            .select("report_count") \
            .eq("user_id", user_id) \
            .eq("report_date", today) \
            .execute()

        count = 0
        if resp.data:
            count = resp.data[0].get("report_count", 0)

        if not ReportRateLimiter.can_generate(user_id, count):
            return False, f"Daily report limit reached ({count}/2). Try again tomorrow."

        return True, ""
    except Exception as e:
        # Graceful: if spend check fails, log and allow
        # In production, you might want to fail closed
        print(f"[WARN] Spend caps check failed: {e}")
        return True, ""


def _increment_usage(user_id: str) -> None:
    """Increment today's report count for the user."""
    if supabase is None:
        return
    try:
        today = date.today().isoformat()
        # Upsert: insert or update
        existing = (
            supabase.table("report_usage")
            .select("report_count")
            .eq("user_id", user_id)
            .eq("report_date", today)
            .execute()
        )

        if existing.data:
            new_count = existing.data[0]["report_count"] + 1
            supabase.table("report_usage").update({"report_count": new_count}).eq(
                "user_id", user_id
            ).eq("report_date", today).execute()
        else:
            supabase.table("report_usage").insert({
                "user_id": user_id,
                "report_date": today,
                "report_count": 1,
            }).execute()
    except Exception as e:
        print(f"[WARN] Failed to increment usage: {e}")


def _upload_pdf_to_storage(pdf_path: str, report_id: str) -> bool:
    """
    Upload the generated PDF to the Supabase Storage 'reports' bucket so the
    public download URL (.../storage/v1/object/public/reports/{id}.pdf) is durable
    and survives server redeploys / preview sleep. Best-effort: on any failure we
    log and return False; the local StaticFiles copy at {backend}/reports/{id}.pdf
    still serves the file, so a report is never lost.
    """
    if supabase is None:
        return False
    try:
        with open(pdf_path, "rb") as fh:
            file_bytes = fh.read()
        # supabase-py v2 storage API; upsert overwrites if the object already exists.
        supabase.storage.from_("reports").upload(
            path=f"{report_id}.pdf",
            file=file_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
        print(f"[STORAGE] Uploaded report {report_id}.pdf to Supabase 'reports' bucket")
        return True
    except Exception as e:
        print(f"[WARN] Supabase Storage upload failed for {report_id}: {e}")
        return False


def _log_outcome(report_data: dict[str, Any], pdf_path: str) -> None:
    """Log report outcome to Supabase cf_reports table."""
    if supabase is None:
        return
    try:
        supabase.table("cf_reports").insert({
            "user_id": report_data.get("user_id"),
            "session_id": report_data.get("session_id"),
            "problem_statement": report_data.get("problem_statement"),
            "steps_completed": report_data.get("steps", []),
            "report_content": report_data,
            "pdf_path": pdf_path,
            "download_url": _get_pdf_generator().get_download_url(
                report_data.get("report_id", "")
            ),
            "conscious_review": report_data.get("conscious_review", ""),
        }).execute()
    except Exception as e:
        print(f"[WARN] Failed to log outcome: {e}")


# ── Endpoints ────────────────────────────────────────────────

@router.post(
    "/cf-computer/report",
    response_model=ReportResponse,
    responses={
        429: {"model": ReportErrorResponse},
        500: {"model": ReportErrorResponse},
    },
    summary="Generate a CF Computer validation report",
    tags=["CF Computer"],
)
async def generate_report(request: ReportRequest) -> JSONResponse:
    """
    Execute the 6-step CF Computer pipeline and return a branded PDF report.

    - Checks spend caps (max 2 reports/day)
    - Runs 6-step bounded pipeline
    - Generates branded PDF
    - Logs outcome to Supabase
    - Returns download URL + structured data
    """
    # ── 1. Spend caps / rate limit check ───────────────────────
    allowed, reason = _check_spend_caps(request.user_id)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "status": "error",
                "error_code": "RATE_LIMIT_EXCEEDED",
                "message": reason,
                "details": {"max_per_day": 2},
            },
        )

    # ── 2. Execute pipeline ────────────────────────────────────
    computer = _get_computer()
    try:
        report_data_obj = await computer.generate_report(
            problem_statement=request.problem_statement,
            user_id=request.user_id,
            session_id=request.session_id,
        )
    except StepExecutionError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error_code": "PIPELINE_FAILURE",
                "message": str(e),
                "details": {},
            },
        )
    except CFComputerError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error_code": "COMPUTER_ERROR",
                "message": str(e),
                "details": {},
            },
        )

    # ── 3. Generate PDF ────────────────────────────────────────
    pdf_gen = _get_pdf_generator()
    try:
        pdf_path = pdf_gen.generate_pdf(report_data_obj.to_dict())
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "status": "error",
                "error_code": "PDF_GENERATION_FAILED",
                "message": f"Failed to generate PDF: {str(e)}",
                "details": {},
            },
        )

    # ── 3b. Upload PDF to durable Supabase Storage (best-effort) ─
    _upload_pdf_to_storage(pdf_path, report_data_obj.report_id)

    # ── 4. Increment usage & log outcome ───────────────────────
    _increment_usage(request.user_id)
    _log_outcome(report_data_obj.to_dict(), pdf_path)

    # ── 5. Build response ──────────────────────────────────────
    download_url = pdf_gen.get_download_url(report_data_obj.report_id)

    response_data = {
        "status": "success",
        "report_id": report_data_obj.report_id,
        "download_url": download_url,
        "steps_completed": [
            {
                "step_number": s.step_number,
                "step_name": s.step_name,
                "success": s.success,
            }
            for s in report_data_obj.steps
        ],
        "conscious_review": report_data_obj.conscious_review,
        "total_cost_usd": report_data_obj.total_cost_usd,
        "total_tokens": report_data_obj.total_tokens,
        "generated_at": report_data_obj.generated_at,
        "v1_disclaimer": report_data_obj.v1_disclaimer,
    }

    return JSONResponse(content=response_data, status_code=status.HTTP_200_OK)


@router.post(
    "/cf-computer/report/stream",
    summary="Generate report with SSE progress streaming",
    tags=["CF Computer"],
)
async def generate_report_stream(request: ReportRequest) -> StreamingResponse:
    """
    Same as POST /cf-computer/report but streams progress via SSE.

    Events:
        event: progress
        data: {"step_number": 1, "step_name": "Frame the Problem", "status": "started"}
    """

    async def event_generator() -> AsyncGenerator[str, None]:
        # Rate limit check
        allowed, reason = _check_spend_caps(request.user_id)
        if not allowed:
            yield f"event: error\ndata: {{\"error_code\": \"RATE_LIMIT_EXCEEDED\", \"message\": \"{reason}\"}}\n\n"
            return

        computer = _get_computer()
        progress_steps: list[dict[str, Any]] = []

        async def progress_callback(step_number: int, step_name: str, status: str) -> None:
            progress_steps.append({"step_number": step_number, "step_name": step_name, "status": status})
            yield f"event: progress\ndata: {{\"step_number\": {step_number}, \"step_name\": \"{step_name}\", \"status\": \"{status}\"}}\n\n"

        # We need to bridge async generator with callback
        # For simplicity, we collect progress and yield at end
        # In production, use asyncio.Queue for real-time streaming
        try:
            report_data_obj = await computer.generate_report(
                problem_statement=request.problem_statement,
                user_id=request.user_id,
                session_id=request.session_id,
            )
        except Exception as e:
            yield f"event: error\ndata: {{\"error_code\": \"PIPELINE_FAILURE\", \"message\": \"{str(e)}\"}}\n\n"
            return

        pdf_gen = _get_pdf_generator()
        try:
            pdf_path = pdf_gen.generate_pdf(report_data_obj.to_dict())
        except Exception as e:
            yield f"event: error\ndata: {{\"error_code\": \"PDF_GENERATION_FAILED\", \"message\": \"{str(e)}\"}}\n\n"
            return

        _upload_pdf_to_storage(pdf_path, report_data_obj.report_id)
        _increment_usage(request.user_id)
        _log_outcome(report_data_obj.to_dict(), pdf_path)

        download_url = pdf_gen.get_download_url(report_data_obj.report_id)
        yield f"event: complete\ndata: {{\"report_id\": \"{report_data_obj.report_id}\", \"download_url\": \"{download_url}\"}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/cf-computer/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok", "service": "cf-computer"}


# ── Main entrypoint (for local dev) ──────────────────────────
