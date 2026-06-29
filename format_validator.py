#!/usr/bin/env python3
"""
format_validator.py
===================
Response format validation for the LNS Conscious AI Platform.

CF (Conscious Friend) has three personas: Mother, Teacher, Friend.
Only the **Teacher** persona enforces a structured 3-Part Micro-Block format.
Mother and Friend are free-form.

Teacher Micro-Block structure
------------------------------
1. **What is true** – A clear, kind statement of reality.
2. **What to notice** – A gentle invitation to self-awareness.
3. **One small step** – A single, actionable micro-movement.

Usage
-----
>>> valid, result = validate_microblock(response)
>>> valid
True

>>> valid, result = enforce_format("Teacher", response)
>>> valid
True
"""

from __future__ import annotations

import logging
import re
from typing import Tuple, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Personas that enforce structured format
STRUCTURED_PERSONAS = {"Teacher", "teacher"}

# Maximum length for a Teacher micro-block (characters)
MAX_MICROBLOCK_LENGTH = 1200


# ---------------------------------------------------------------------------
# Validation: 3-Part Micro-Block
# ---------------------------------------------------------------------------

def validate_microblock(response: str) -> Tuple[bool, Union[str, None]]:
    """
    Check whether a response follows the 3-Part Micro-Block structure.

    The validator looks for **conceptual presence** of the three parts,
    not rigid headers.  This allows CF to sound natural while remaining
    structurally grounded.

    Parameters
    ----------
    response: str
        The raw text response from CF (Teacher persona).

    Returns
    -------
    Tuple[bool, Union[str, None]]
        (True, None) if valid.
        (False, error_message) if the structure is missing or malformed.
    """
    if not response or not response.strip():
        return False, "Response is empty."

    text = response.strip()

    if len(text) > MAX_MICROBLOCK_LENGTH:
        return False, f"Response too long ({len(text)} chars). Max: {MAX_MICROBLOCK_LENGTH}."

    # --- Part 1: What is true ---
    # Detects: clear statements of reality, acknowledgment, or truth-telling
    truth_patterns = [
        r"\b(what is true|the truth is|here is what is true|what is real|what matters is)\b",
        r"\b(it is true that|it is okay that|it makes sense that|it is understandable that)\b",
        r"\b(you are not alone|this is hard|this is real|this is valid|this is part of being human)\b",
    ]
    has_truth = any(re.search(p, text, re.IGNORECASE) for p in truth_patterns)

    # --- Part 2: What to notice ---
    # Detects: invitations to awareness, observation, or curiosity
    notice_patterns = [
        r"\b(what to notice|notice|pay attention to|observe|be curious about|what do you notice)\b",
        r"\b(what stands out|what feels alive|what is present for you|what is happening inside)\b",
        r"\b(where do you feel|what do you sense|what shifts|what arises|what emerges)\b",
    ]
    has_notice = any(re.search(p, text, re.IGNORECASE) for p in notice_patterns)

    # --- Part 3: One small step ---
    # Detects: actionable invitation, micro-movement, or experiment
    step_patterns = [
        r"\b(one small step|a small step|one thing|try this|what if you|experiment with|practice)\b",
        r"\b(begin by|start with|take a moment|pause and|try saying|try writing|try noticing)\b",
        r"\b(your next step|a next step|the smallest step|just one thing|one gentle move)\b",
    ]
    has_step = any(re.search(p, text, re.IGNORECASE) for p in step_patterns)

    # Relaxed validation: at least 2 of 3 parts must be present
    # This prevents false positives while maintaining structure
    parts_found = sum([has_truth, has_notice, has_step])

    if parts_found >= 2:
        logger.debug("Micro-Block validation passed: %d/3 parts found", parts_found)
        return True, None

    missing = []
    if not has_truth:
        missing.append("'What is true' (clear statement of reality)")
    if not has_notice:
        missing.append("'What to notice' (invitation to awareness)")
    if not has_step:
        missing.append("'One small step' (actionable micro-movement)")

    error_msg = (
        f"Teacher response missing structured parts ({parts_found}/3 found). "
        f"Missing: {', '.join(missing)}. "
        f"Please rewrite as a 3-Part Micro-Block."
    )
    logger.warning("Micro-Block validation failed: %s", error_msg)
    return False, error_msg


# ---------------------------------------------------------------------------
# Enforcement wrapper
# ---------------------------------------------------------------------------

def enforce_format(persona: str, response: str) -> Tuple[bool, Union[str, str]]:
    """
    Enforce response format based on persona.

    * **Teacher** -> validate 3-Part Micro-Block.
    * **Mother / Friend** -> always pass (free-form).

    Parameters
    ----------
    persona: str
        One of "Mother", "Teacher", "Friend" (case-insensitive).
    response: str
        The CF-generated response text.

    Returns
    -------
    Tuple[bool, Union[str, str]]
        (True, response)        if valid or persona is free-form.
        (False, error_message)  if validation fails for Teacher.
    """
    normalized = persona.strip()

    if normalized not in STRUCTURED_PERSONAS:
        # Mother and Friend are free-form — no validation needed
        logger.debug("Format enforcement skipped for persona: %s", normalized)
        return True, response

    # Teacher persona: strict validation
    valid, result = validate_microblock(response)
    if valid:
        return True, response
    return False, result


# ---------------------------------------------------------------------------
# Helper: format hint for LLM prompt injection
# ---------------------------------------------------------------------------

def get_teacher_format_hint() -> str:
    """
    Return the system prompt snippet that instructs the Teacher persona
    to use the 3-Part Micro-Block structure.  Inject this into the
    Teacher system prompt at runtime.
    """
    return (
        "When you respond as Teacher, use the 3-Part Micro-Block structure:\n"
        "1. What is true – A clear, kind statement of reality.\n"
        "2. What to notice – A gentle invitation to self-awareness.\n"
        "3. One small step – A single, actionable micro-movement.\n"
        "Keep each part concise. The total response should be under 1200 characters."
    )


# ---------------------------------------------------------------------------
# Helper: auto-fix attempt (optional, for future use)
# ---------------------------------------------------------------------------

def attempt_fix(response: str) -> str:
    """
    Attempt a lightweight rewrite of a non-conforming Teacher response
    by inserting structural markers.  This is a fallback and may be
    replaced with a model-based rewrite in production.

    Returns the potentially modified response.
    """
    text = response.strip()
    if not text:
        return text

    # If the response is one long paragraph, try to split into three parts
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) >= 3 and not re.search(r'\n\n', text):
        part1 = " ".join(sentences[:len(sentences)//3])
        part2 = " ".join(sentences[len(sentences)//3:2*len(sentences)//3])
        part3 = " ".join(sentences[2*len(sentences)//3:])
        return f"{part1}\n\n{part2}\n\n{part3}"

    return text
