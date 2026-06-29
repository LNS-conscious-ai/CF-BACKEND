"""
CF Computer v1 — Execution Engine
LNS Conscious AI Platform

A bounded 6-step pipeline that generates a branded validation report PDF.
CF Computer collapses execution, never discovery.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx


# ───────────────────────────────────────────────────────────────
# DeepInfra / Model Configuration
# ───────────────────────────────────────────────────────────────

DEEPINFRA_API_KEY = os.environ.get("DEEPINFRA_API_KEY", "")
DEEPINFRA_BASE_URL = "https://api.deepinfra.com/v1/openai"

# Primary model: Kimi K2.5 via DeepInfra
PRIMARY_MODEL = os.environ.get("CF_COMPUTER_MODEL", "moonshotai/kimi-k2-5")

# Fallback model: DeepSeek (single-line fallback per architecture spec)
FALLBACK_MODEL = os.environ.get("CF_COMPUTER_FALLBACK_MODEL", "deepseek/deepseek-chat")

# Cost per 1K tokens (USD) — approximate, for tracking
COST_PER_1K_TOKENS: dict[str, float] = {
    "moonshotai/kimi-k2-5": 0.003,
    "deepseek/deepseek-chat": 0.0005,
}

DEFAULT_TIMEOUT = 60.0


# ───────────────────────────────────────────────────────────────
# Data Classes
# ───────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    """Result of a single pipeline step."""

    step_number: int
    step_name: str
    content: str
    model_used: str
    tokens_used: int = 0
    cost_usd: float = 0.0
    success: bool = True
    error: str | None = None
    duration_ms: int = 0


@dataclass
class CFReportData:
    """Structured output from the 6-step pipeline, ready for PDF generation."""

    report_id: str
    user_id: str
    session_id: str
    problem_statement: str
    steps: list[StepResult] = field(default_factory=list)
    conscious_review: str = ""
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    generated_at: str = ""
    v1_disclaimer: str = (
        "This is a starting draft to refine with CF — not gospel."
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize report data to a JSON-friendly dict."""
        return {
            "report_id": self.report_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "problem_statement": self.problem_statement,
            "steps": [
                {
                    "step_number": s.step_number,
                    "step_name": s.step_name,
                    "content": s.content,
                    "model_used": s.model_used,
                    "tokens_used": s.tokens_used,
                    "cost_usd": s.cost_usd,
                    "success": s.success,
                    "error": s.error,
                    "duration_ms": s.duration_ms,
                }
                for s in self.steps
            ],
            "conscious_review": self.conscious_review,
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
            "generated_at": self.generated_at,
            "v1_disclaimer": self.v1_disclaimer,
        }


# ───────────────────────────────────────────────────────────────
# Exceptions
# ───────────────────────────────────────────────────────────────

class CFComputerError(Exception):
    """Base exception for CF Computer errors."""

    pass


class RateLimitError(CFComputerError):
    """Raised when user exceeds max reports per day."""

    pass


class LLMCallError(CFComputerError):
    """Raised when both primary and fallback LLM calls fail."""

    pass


class StepExecutionError(CFComputerError):
    """Raised when a pipeline step fails and cannot degrade gracefully."""

    pass


# ───────────────────────────────────────────────────────────────
# CF Computer Engine
# ───────────────────────────────────────────────────────────────

class CFComputer:
    """
    CF Computer v1 — Bounded 6-step execution engine.

    Generates a structured validation report for a confirmed problem statement.
    No loops. No discovery. Hard stop at 6 steps.
    """

    # ── Step Prompts (editable constants) ──────────────────────

    STEP_1_FRAME_PROMPT = """You are CF Computer — the execution engine of LNS Conscious AI Platform.

TASK: Frame the user's confirmed problem clearly and powerfully.

INPUT PROBLEM: {problem_statement}

RULES:
- Restate the problem in 2-3 sharp sentences. Be precise, not vague.
- Strip jargon. A 15-year-old should understand it.
- Identify the core pain — what makes this problem costly or urgent?
- End with one sentence that captures WHY this matters to the human experiencing it.
- Max 150 words. No bullet lists. Plain paragraphs only.
- Do NOT do discovery. Do NOT ask questions. The problem is already confirmed.
"""

    STEP_2_MARKET_SNAPSHOT_PROMPT = """You are CF Computer — the execution engine of LNS Conscious AI Platform.

TASK: Explain why this problem matters NOW — timeliness and market relevance.

INPUT PROBLEM: {problem_statement}

CONTEXT FROM STEP 1: {step_1_output}

RULES:
- What trend, technology shift, or cultural change makes this problem urgent today?
- Reference 1-2 real signals (news, data, behavior shifts) if possible. If uncertain, say "signal not verified" rather than invent data.
- Why is the window open now? Why might it close?
- Max 150 words. No bullet lists. Plain paragraphs only.
- Tone: sharp, evidence-leaning, not hype.
"""

    STEP_3_COMPETITOR_SCAN_PROMPT = """You are CF Computer — the execution engine of LNS Conscious AI Platform.

TASK: Scan 3-5 existing tools or solutions and identify the gap they leave.

INPUT PROBLEM: {problem_statement}

CONTEXT: {context}

RULES:
- Name 3-5 real competitors or existing solutions (apps, services, methods).
- For each: What do they do? What gap do they leave for the user?
- End with the unified gap: what is NO ONE solving well for this specific problem?
- Be honest about incumbents. Do not trash them unfairly. Focus on the gap.
- Max 200 words. Use a simple numbered list for competitors, then one closing paragraph.
- Do NOT hallucinate products. If uncertain, use generic categories (e.g., "traditional consulting firms") rather than fake names.
"""

    STEP_4_TARGET_USER_PROMPT = """You are CF Computer — the execution engine of LNS Conscious AI Platform.

TASK: Define the target user and the smallest first version to build.

INPUT PROBLEM: {problem_statement}

CONTEXT: {context}

RULES:
- Who is the ONE person who feels this problem most acutely? Describe them in 2-3 sentences (demographics + psychographics).
- What is the SMALLEST thing they could use in 24 hours? Not a full product. A version, a test, a prototype.
- What would "success" look like for that first version? How would the user know it works?
- Max 150 words. Plain paragraphs.
- Be concrete. "A landing page with a waitlist" is better than "a platform."
"""

    STEP_5_ACTION_PLAN_PROMPT = """You are CF Computer — the execution engine of LNS Conscious AI Platform.

TASK: Create a 7-day action plan with 3 concrete, doable next steps.

INPUT PROBLEM: {problem_statement}

CONTEXT: {context}

RULES:
- Provide exactly 3 steps. Each step must be doable in 1-2 days by one person.
- For each step: What to do, why it matters, and what "done" looks like.
- Step 1 should be the fastest win (validation, not building).
- Step 2 should deepen the signal (talk to users, test assumptions).
- Step 3 should be the first small build or experiment.
- Max 200 words. Use a simple numbered list.
- Do not include generic advice like "do market research." Be specific to THIS problem.
"""

    CONSCIOUS_REVIEW_PROMPT = """You are CF Computer — the execution engine of LNS Conscious AI Platform.

TASK: Honest conscious review — does this plan align with who the user is?

INPUT PROBLEM: {problem_statement}

FULL PLAN CONTEXT: {context}

RULES:
- This is NOT a cheerleading section. Be honest. Be kind.
- Ask 2-3 reflective questions that help the user check alignment:
  * Does this plan energize you or drain you?
  * Is this solving a problem you genuinely care about, or one that sounds impressive?
  * Does the first version feel doable with your current energy, skills, and life situation?
- If the plan feels misaligned, say so gently. Suggest what to revisit.
- End with one sentence of genuine encouragement.
- Max 150 words. Plain paragraphs. No bullet lists.
- This is the SOUL of CF Computer. Make it feel like a conscious friend, not a productivity bot.
"""

    # ── Constructor ──────────────────────────────────────────────

    def __init__(
        self,
        api_key: str | None = None,
        primary_model: str | None = None,
        fallback_model: str | None = None,
    ) -> None:
        """
        Initialize CF Computer engine.

        Args:
            api_key: DeepInfra API key. Defaults to env var DEEPINFRA_API_KEY.
            primary_model: Primary LLM model. Defaults to env var.
            fallback_model: Fallback LLM model. Defaults to env var.
        """
        self.api_key = api_key or DEEPINFRA_API_KEY
        self.primary_model = primary_model or PRIMARY_MODEL
        self.fallback_model = fallback_model or FALLBACK_MODEL
        self.base_url = DEEPINFRA_BASE_URL

        if not self.api_key:
            raise CFComputerError(
                "DEEPINFRA_API_KEY not set. Provide api_key or set env var."
            )

    # ── Public API ─────────────────────────────────────────────

    async def generate_report(
        self,
        problem_statement: str,
        user_id: str,
        session_id: str,
        progress_callback: Any | None = None,
    ) -> CFReportData:
        """
        Execute the bounded 6-step pipeline and return structured report data.

        Args:
            problem_statement: The confirmed problem from CF conversation.
            user_id: UUID string of the user.
            session_id: UUID string of the conversation session.
            progress_callback: Optional async callable(step_number, step_name, status)
                for SSE streaming progress updates.

        Returns:
            CFReportData with all 6 steps + conscious review.

        Raises:
            StepExecutionError: If a critical step fails and cannot degrade.
        """
        report_id = str(uuid.uuid4())
        report_data = CFReportData(
            report_id=report_id,
            user_id=user_id,
            session_id=session_id,
            problem_statement=problem_statement,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        # Step 1: Frame the problem
        step_1 = await self._execute_step(
            step_number=1,
            step_name="Frame the Problem",
            prompt_template=self.STEP_1_FRAME_PROMPT,
            problem_statement=problem_statement,
            context="",
            progress_callback=progress_callback,
        )
        report_data.steps.append(step_1)
        if not step_1.success:
            raise StepExecutionError(f"Step 1 failed: {step_1.error}")

        # Step 2: Market snapshot
        step_2 = await self._execute_step(
            step_number=2,
            step_name="Market Snapshot",
            prompt_template=self.STEP_2_MARKET_SNAPSHOT_PROMPT,
            problem_statement=problem_statement,
            context=step_1.content,
            progress_callback=progress_callback,
        )
        report_data.steps.append(step_2)

        # Step 3: Competitor scan
        context_3 = f"Step 1:\n{step_1.content}\n\nStep 2:\n{step_2.content}"
        step_3 = await self._execute_step(
            step_number=3,
            step_name="Competitor Scan",
            prompt_template=self.STEP_3_COMPETITOR_SCAN_PROMPT,
            problem_statement=problem_statement,
            context=context_3,
            progress_callback=progress_callback,
        )
        report_data.steps.append(step_3)

        # Step 4: Target user + first version
        context_4 = f"{context_3}\n\nStep 3:\n{step_3.content}"
        step_4 = await self._execute_step(
            step_number=4,
            step_name="Target User & First Version",
            prompt_template=self.STEP_4_TARGET_USER_PROMPT,
            problem_statement=problem_statement,
            context=context_4,
            progress_callback=progress_callback,
        )
        report_data.steps.append(step_4)

        # Step 5: 7-day action plan
        context_5 = f"{context_4}\n\nStep 4:\n{step_4.content}"
        step_5 = await self._execute_step(
            step_number=5,
            step_name="7-Day Action Plan",
            prompt_template=self.STEP_5_ACTION_PLAN_PROMPT,
            problem_statement=problem_statement,
            context=context_5,
            progress_callback=progress_callback,
        )
        report_data.steps.append(step_5)

        # Step 6: Conscious review (special prompt, always executed)
        context_6 = f"{context_5}\n\nStep 5:\n{step_5.content}"
        step_6 = await self._execute_step(
            step_number=6,
            step_name="Conscious Review",
            prompt_template=self.CONSCIOUS_REVIEW_PROMPT,
            problem_statement=problem_statement,
            context=context_6,
            progress_callback=progress_callback,
        )
        report_data.steps.append(step_6)
        report_data.conscious_review = step_6.content

        # Aggregate costs
        report_data.total_cost_usd = sum(s.cost_usd for s in report_data.steps)
        report_data.total_tokens = sum(s.tokens_used for s in report_data.steps)

        return report_data

    # ── Internal: Step Execution ───────────────────────────────

    async def _execute_step(
        self,
        step_number: int,
        step_name: str,
        prompt_template: str,
        problem_statement: str,
        context: str,
        progress_callback: Any | None = None,
    ) -> StepResult:
        """
        Execute a single pipeline step with LLM call + fallback.

        Args:
            step_number: 1-6.
            step_name: Human-readable step name.
            prompt_template: The prompt template with {problem_statement} and {context}.
            problem_statement: The user's confirmed problem.
            context: Accumulated outputs from previous steps.
            progress_callback: Optional callback for progress updates.

        Returns:
            StepResult with content, cost, and success flag.
        """
        import time

        start_time = time.time()

        # Notify progress
        if progress_callback is not None:
            try:
                await progress_callback(step_number, step_name, "started")
            except Exception:
                pass  # Callback errors must not break the pipeline

        # Build prompt
        prompt = prompt_template.format(
            problem_statement=problem_statement,
            context=context,
            step_1_output=context.split("Step 1:")[-1].split("Step 2:")[0].strip() if "Step 1:" in context else context,
        )

        # Call LLM with fallback
        content, model_used, tokens_used, error = await self._call_llm_with_fallback(prompt)

        duration_ms = int((time.time() - start_time) * 1000)

        if error:
            # Graceful degradation: return error result but don't halt pipeline
            # The caller decides if the step is critical enough to raise
            result = StepResult(
                step_number=step_number,
                step_name=step_name,
                content=f"[Step {step_number} failed: {error}]",
                model_used=model_used or "none",
                tokens_used=0,
                cost_usd=0.0,
                success=False,
                error=error,
                duration_ms=duration_ms,
            )
        else:
            cost = self._estimate_cost(model_used, tokens_used)
            result = StepResult(
                step_number=step_number,
                step_name=step_name,
                content=content or "",
                model_used=model_used,
                tokens_used=tokens_used,
                cost_usd=cost,
                success=True,
                error=None,
                duration_ms=duration_ms,
            )

        # Notify progress
        if progress_callback is not None:
            try:
                status = "completed" if result.success else "failed"
                await progress_callback(step_number, step_name, status)
            except Exception:
                pass

        return result

    # ── Internal: LLM Call with Fallback ───────────────────────

    async def _call_llm_with_fallback(
        self, prompt: str
    ) -> tuple[str | None, str | None, int, str | None]:
        """
        Call primary model. If it fails, single-line fallback to DeepSeek.

        Args:
            prompt: The full prompt text.

        Returns:
            Tuple of (content, model_used, tokens_used, error).
            If both fail, error is set and content is None.
        """
        # Try primary model
        content, model_used, tokens_used, error = await self._call_llm(
            prompt, model=self.primary_model
        )
        if content is not None:
            return content, model_used, tokens_used, None

        # Single-line fallback: DeepSeek
        try:
            content_fb, model_fb, tokens_fb, error_fb = await self._call_llm(
                prompt, model=self.fallback_model
            )
            if content_fb is not None:
                return content_fb, model_fb, tokens_fb, None
            return None, None, 0, f"Primary: {error}; Fallback: {error_fb}"
        except Exception as e:
            return None, None, 0, f"Primary: {error}; Fallback: {str(e)}"

    async def _call_llm(
        self, prompt: str, model: str
    ) -> tuple[str | None, str | None, int, str | None]:
        """
        Make a single LLM call to DeepInfra.

        Args:
            prompt: The prompt text.
            model: Model identifier string.

        Returns:
            Tuple of (content, model_used, tokens_used, error).
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are CF Computer, the execution engine of LNS Conscious AI Platform. "
                        "You generate structured, actionable validation reports. "
                        "You never do discovery — you only execute on confirmed problems. "
                        "You are concise, honest, and warm."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 800,
        }

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

                choice = data.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content", "")

                usage = data.get("usage", {})
                tokens_used = usage.get("total_tokens", 0)

                return content, model, tokens_used, None

        except httpx.HTTPStatusError as e:
            return None, None, 0, f"HTTP {e.response.status_code}: {e.response.text}"
        except httpx.RequestError as e:
            return None, None, 0, f"Request error: {str(e)}"
        except Exception as e:
            return None, None, 0, f"Unexpected error: {str(e)}"

    # ── Internal: Cost Estimation ──────────────────────────────

    def _estimate_cost(self, model: str | None, tokens: int) -> float:
        """Estimate call cost in USD."""
        if not model:
            return 0.0
        rate = COST_PER_1K_TOKENS.get(model, 0.003)
        return (tokens / 1000.0) * rate


# ───────────────────────────────────────────────────────────────
# Rate Limiting Helper (Simple Counter)
# ───────────────────────────────────────────────────────────────

class ReportRateLimiter:
    """
    Simple in-memory rate limiter: max 2 reports per user per day.

    In production, this is backed by Supabase `report_usage` table.
    This class provides the interface; the endpoint layer does the DB check.
    """

    MAX_REPORTS_PER_DAY = 2

    @staticmethod
    def can_generate(user_id: str, today_count: int) -> bool:
        """Check if user can generate another report today."""
        return today_count < ReportRateLimiter.MAX_REPORTS_PER_DAY

    @staticmethod
    def remaining(today_count: int) -> int:
        """Reports remaining for today."""
        return max(0, ReportRateLimiter.MAX_REPORTS_PER_DAY - today_count)
