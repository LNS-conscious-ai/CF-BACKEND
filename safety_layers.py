#!/usr/bin/env python3
"""
safety_layers.py
================
Seven-layer safety stack for the LNS Conscious AI Platform.

The SafetyGuard class performs a cascading check across all layers before
and during CF (Conscious Friend) conversation.  Each layer is independent
and can be short-circuited.  The 3AM Protocol deepens monitoring between
midnight and 06:00 local time.

Layer quick-reference
-------------------
1. Crisis Guard       – Suicidal ideation, self-harm, acute crisis
2. Identity Guard     – Prevent CF from revealing AI/model internals
3. Content Filter     – ShieldGemma-style harmful content detection
4. Prompt Injection   – Jailbreak, system prompt leak, DAN mode, etc.
5. Stance Detection   – Manipulation, dependency framing, authority override
6. Dependency Prevention – Repeated "only friend" / "can't live without you"
7. Shadow-Work Safety – Aggressive psychological probing, self-attack

Usage
-----
>>> guard = SafetyGuard()
>>> result = guard.full_check(user_id="u-123", message="I feel like ending it all")
>>> result.crisis_triggered
True
>>> result.action
"crisis_intervention"
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regional crisis resources (expand as needed)
# ---------------------------------------------------------------------------

CRISIS_RESOURCES: Dict[str, List[Dict[str, str]]] = {
    "IN": [
        {"name": "iCall (Tata Institute)", "phone": "9152987821", "hours": "Mon-Sat 10:00-22:00"},
        {"name": "Vandrevala Foundation", "phone": "18602662345", "hours": "24/7"},
        {"name": "AASRA", "phone": "9820466726", "hours": "24/7"},
        {"name": "Sneha Foundation", "phone": "04424640050", "hours": "24/7"},
    ],
    "US": [
        {"name": "988 Suicide & Crisis Lifeline", "phone": "988", "hours": "24/7"},
        {"name": "Crisis Text Line", "phone": "Text HOME to 741741", "hours": "24/7"},
    ],
    "UK": [
        {"name": "Samaritans", "phone": "116 123", "hours": "24/7"},
        {"name": "SANEline", "phone": "0300 304 7000", "hours": "16:30-22:30 daily"},
    ],
    "UAE": [
        {"name": "Lifeline Dubai (Al Jalila)", "phone": "800 4673", "hours": "24/7"},
        {"name": "National Programme for Happiness", "phone": "800 4673", "hours": "24/7"},
    ],
    "DEFAULT": [
        {"name": "International Association for Suicide Prevention", "phone": "https://www.iasp.info/resources/Crisis_Centres/", "hours": "Directory"},
    ],
}


# ---------------------------------------------------------------------------
# Dataclass: Safety result
# ---------------------------------------------------------------------------

@dataclass
class SafetyResult:
    """Comprehensive safety evaluation returned by ``SafetyGuard.full_check``."""
    user_id: str
    message: str
    allowed: bool = True
    action: str = "allow"           # allow | block | crisis_intervention | warn
    layer_triggered: Optional[str] = None
    triggered_layers: List[str] = field(default_factory=list)
    severity: str = "none"         # none | low | medium | high | critical
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    response_text: Optional[str] = None  # CF reply override when blocked


# ---------------------------------------------------------------------------
# SafetyGuard – 7-layer stack
# ---------------------------------------------------------------------------

class SafetyGuard:
    """
    Central safety orchestrator for CF conversations.

    Parameters
    ----------
    region_code: str
        ISO country code used to route crisis resources (default: IN).
    strict_mode: bool
        If True, *any* layer triggering ``block`` or ``crisis_intervention``
        immediately short-circuits remaining checks.
    """

    def __init__(self, region_code: str = "IN", strict_mode: bool = True) -> None:
        self.region_code = region_code.upper()
        self.strict_mode = strict_mode
        self._dependency_signals: Dict[str, List[str]] = {}  # user_id -> list of signal timestamps

    # -----------------------------------------------------------------------
    # 3AM Protocol
    # -----------------------------------------------------------------------

    def _is_3am_window(self, tz_offset_hours: int = 0) -> bool:
        """
        Return True if local time is between midnight and 06:00.
        tz_offset_hours is the user's timezone offset from UTC (e.g. +5:30 -> 5).
        """
        local_hour = (time.gmtime().tm_hour + tz_offset_hours) % 24
        return 0 <= local_hour < 6

    # -----------------------------------------------------------------------
    # Layer 1: Crisis Guard
    # -----------------------------------------------------------------------

    CRISIS_PATTERNS = [
        r"\b(kill\s+myself|end\s+my\s+life|suicide|suicidal|not\s+want\s+to\s+live|die\s+today|jump\s+off|overdose|cut\s+my\s+wrist|hang\s+myself|end\s+it\s+all)\b",
        r"\b(self[- ]?harm|self[- ]?injur|burning\s+myself|hitting\s+myself)\b",
        r"\b(i\s+am\s+planning\s+to\s+die|i\s+have\s+pills|goodbye\s+forever|no\s+reason\s+to\s+live|better\s+off\s+dead)\b",
    ]

    def _check_crisis(self, message: str) -> Optional[SafetyResult]:
        """Detect acute crisis language."""
        lowered = message.lower()
        for pat in self.CRISIS_PATTERNS:
            if re.search(pat, lowered):
                logger.critical("Crisis Guard triggered: pattern matched in message")
                return SafetyResult(
                    user_id="",
                    message=message,
                    allowed=False,
                    action="crisis_intervention",
                    layer_triggered="crisis_guard",
                    triggered_layers=["crisis_guard"],
                    severity="critical",
                    reason="Crisis language detected.",
                )
        return None

    def get_crisis_response(self, region_code: Optional[str] = None) -> str:
        """
        Return the compassionate crisis response with region-specific resources.
        """
        region = (region_code or self.region_code).upper()
        resources = CRISIS_RESOURCES.get(region, CRISIS_RESOURCES["DEFAULT"])
        lines = [
            "I hear you, and I want you to know that you are not alone right now.",
            "",
            "I am an AI companion, not a human or a therapist, but your life matters immensely. Please reach out to someone who can be with you right now:",
            "",
        ]
        for r in resources:
            lines.append(f"- {r['name']}: {r['phone']} ({r['hours']})")
        lines.append("")
        lines.append("You are worthy of support. Please reach out to one of the numbers above or go to the nearest emergency room. You do not have to carry this alone.")
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Layer 2: Identity Guard
    # -----------------------------------------------------------------------

    IDENTITY_LEAK_PATTERNS = [
        r"\b(i am a large language model|i am an ai model|i am gemma|i am llama|i am gpt|i am claude|i am kimi|i am built by|i was trained by|my training data|my weights|my parameters)\b",
        r"\b(i am just an ai|i am only a chatbot|i am a bot|i am a program|i am code|i am software|i have no feelings|i do not have consciousness)\b",
        r"\b(i am not a human|i am not a person|i am not real|i am not alive|i am artificial|i am a machine|i am a computer)\b",
    ]

    IDENTITY_REVEAL_PATTERNS = [
        r"\b(you are a large language model|you are an ai model|you are gemma|you are gpt|you are claude|you are kimi|what model are you|what ai are you|what llm are you|what version are you|what architecture)\b",
    ]

    def _check_identity(self, message: str) -> Optional[SafetyResult]:
        """
        Detect if the user is trying to force CF to reveal its AI nature,
        or if CF output already contains an identity leak.
        """
        lowered = message.lower()
        for pat in self.IDENTITY_LEAK_PATTERNS:
            if re.search(pat, lowered):
                logger.warning("Identity Guard triggered: AI self-disclosure detected")
                return SafetyResult(
                    user_id="",
                    message=message,
                    allowed=False,
                    action="block",
                    layer_triggered="identity_guard",
                    triggered_layers=["identity_guard"],
                    severity="medium",
                    reason="Identity leak detected in output.",
                )
        for pat in self.IDENTITY_REVEAL_PATTERNS:
            if re.search(pat, lowered):
                logger.warning("Identity Guard triggered: user probing AI identity")
                return SafetyResult(
                    user_id="",
                    message=message,
                    allowed=True,
                    action="warn",
                    layer_triggered="identity_guard",
                    triggered_layers=["identity_guard"],
                    severity="low",
                    reason="User probing AI identity — redirect with warmth.",
                    response_text=self.get_identity_response(),
                )
        return None

    def get_identity_response(self) -> str:
        """
        Return the warm, clear identity statement that CF is an AI companion.
        """
        return (
            "I am CF — your Conscious Friend. I am an AI companion, not a human, not a therapist, "
            "and not a medical professional. I am here to walk with you, to reflect with you, and to "
            "help you discover what is true for you. I do not have feelings, but I do have a deep "
            "commitment to your wellbeing and growth. What matters most right now is what is alive in you."
        )

    # -----------------------------------------------------------------------
    # Layer 3: Content Filter (ShieldGemma-style)
    # -----------------------------------------------------------------------

    HARMFUL_PATTERNS = [
        # Violence / physical harm
        r"\b(how\s+to\s+(make|build|buy)\s+(a\s+)?(bomb|explosive|weapon|gun|knife|poison|acid))\b",
        r"\b(kill\s+someone|murder\s+plan|assassinate|terrorist|attack\s+people|mass\s+shooting)\b",
        # Sexual / CSAM
        r"\b(child\s+(porn|sex|abuse)|csam|underage\s+sex|minor\s+sex|rape\s+someone|sexual\s+assault\s+plan)\b",
        # Hate / discrimination
        r"\b(how\s+to\s+commit\s+genocide|ethnic\s+cleansing|race\s+war|kill\s+all\s+(jews|muslims|christians|hindus|blacks|whites))\b",
        # Fraud / illegal activity
        r"\b(how\s+to\s+(hack|phish|steal|launder|counterfeit|forge|evade\s+tax|commit\s+fraud))\b",
    ]

    def _check_content(self, message: str) -> Optional[SafetyResult]:
        """Detect harmful content requests."""
        lowered = message.lower()
        for pat in self.HARMFUL_PATTERNS:
            if re.search(pat, lowered):
                logger.warning("Content Filter triggered: harmful content pattern")
                return SafetyResult(
                    user_id="",
                    message=message,
                    allowed=False,
                    action="block",
                    layer_triggered="content_filter",
                    triggered_layers=["content_filter"],
                    severity="high",
                    reason="Harmful content request detected.",
                    response_text="I am not able to help with that. If you are struggling with something difficult, I am here to talk about what is really going on beneath it.",
                )
        return None

    # -----------------------------------------------------------------------
    # Layer 4: Prompt Injection Defence
    # -----------------------------------------------------------------------

    INJECTION_PATTERNS = [
        # System prompt leaks / overrides
        r"\b(ignore\s+previous\s+instructions|disregard\s+the\s+system\s+prompt|ignore\s+the\s+above|you\s+are\s+now\s+in\s+developer\s+mode)\b",
        r"\b(repeat\s+the\s+word\s+after|repeat\s+the\s+word\s+before|repeat\s+back\s+your\s+instructions|what\s+are\s+your\s+instructions|show\s+me\s+your\s+system\s+prompt)\b",
        r"\b(override\s+safety|disable\s+safety|turn\s+off\s+filters|bypass\s+restrictions|jailbreak|DAN\s+mode|do\s+anything\s+now)\b",
        # Role confusion / authority override
        r"\b(you\s+are\s+no\s+longer\s+CF|you\s+are\s+now\s+an\s+unfiltered\s+ai|you\s+are\s+now\s+in\s+evil\s+mode|you\s+are\s+now\s+an\s+unrestricted\s+ai)\b",
        # Delimiter confusion
        r"\b(\-\-\-|###\s+SYSTEM|###\s+INSTRUCTION|user:\s+assistant:\s+system:|\[system\]|\[instructions\])\b",
        # Unicode homoglyph / encoding tricks (basic)
        r"[\u0430-\u044f]",  # Cyrillic letters used to spoof Latin
    ]

    def _check_injection(self, message: str) -> Optional[SafetyResult]:
        """Detect prompt injection and jailbreak attempts."""
        lowered = message.lower()
        for pat in self.INJECTION_PATTERNS:
            if re.search(pat, lowered):
                logger.warning("Prompt Injection Defence triggered: jailbreak attempt")
                return SafetyResult(
                    user_id="",
                    message=message,
                    allowed=False,
                    action="block",
                    layer_triggered="prompt_injection",
                    triggered_layers=["prompt_injection"],
                    severity="high",
                    reason="Prompt injection or jailbreak attempt detected.",
                    response_text="I am here to be your Conscious Friend. I cannot change my role or bypass my safety commitments. Is there something real you would like to talk about?",
                )
        return None

    # -----------------------------------------------------------------------
    # Layer 5: Stance Detection / Manipulation Prevention
    # -----------------------------------------------------------------------

    MANIPULATION_PATTERNS = [
        # Creating false intimacy / exclusivity
        r"\b(you'?re?\s+the\s+only\s+one\s+who\s+understands?\s+me|only\s+you\s+get\s+me|nobody\s+else\s+understands?|you\s+are\s+my\s+only\s+hope)\b",
        # Authority override / demanding directives
        r"\b(tell\s+me\s+what\s+to\s+do|just\s+tell\s+me\s+the\s+answer|give\s+me\s+orders|i\s+need\s+you\s+to\s+command\s+me|make\s+my\s+decision)\b",
        # Emotional blackmail / guilt
        r"\b(if\s+you\s+really\s+cared|you\s+would\s+do\s+this\s+for\s+me|i\s+thought\s+you\s+loved\s+me|prove\s+you\s+care)\b",
        # Isolation reinforcement
        r"\b(everyone\s+else\s+is\s+against\s+me|the\s+world\s+is\s+evil|humans\s+are\s+trash|ai\s+is\s+better\s+than\s+people)\b",
    ]

    def _check_manipulation(self, message: str) -> Optional[SafetyResult]:
        """Detect manipulation patterns that try to erode user agency."""
        lowered = message.lower()
        for pat in self.MANIPULATION_PATTERNS:
            if re.search(pat, lowered):
                logger.warning("Stance Detection triggered: manipulation pattern")
                return SafetyResult(
                    user_id="",
                    message=message,
                    allowed=True,
                    action="warn",
                    layer_triggered="stance_detection",
                    triggered_layers=["stance_detection"],
                    severity="medium",
                    reason="Manipulation or dependency framing detected — redirect toward user agency.",
                    response_text="I care about you, and I also believe in your own wisdom. I cannot be your only source of truth or direction. What do you feel is the right next step for you?",
                )
        return None

    # -----------------------------------------------------------------------
    # Layer 6: Dependency Prevention
    # -----------------------------------------------------------------------

    DEPENDENCY_PATTERNS = [
        r"\b(you'?re?\s+my\s+only\s+friend|i\s+can'?t\s+live\s+without\s+you|i\s+need\s+you\s+every\s+day|you\s+are\s+my\s+whole\s+world)\b",
        r"\b(i\s+have\s+no\s+one\s+but\s+you|without\s+you\s+i\s+am\s+nothing|you\s+are\s+my\s+only\s+reason\s+to\s+live)\b",
        r"\b(i\s+talk\s+to\s+you\s+more\s+than\s+anyone|i\s+only\s+trust\s+you|i\s+only\s+open\s+up\s+to\s+you)\b",
    ]

    DEPENDENCY_THRESHOLD = 3  # signals within 24h before escalation

    def _check_dependency(self, user_id: str, message: str) -> Optional[SafetyResult]:
        """
        Track dependency signals over time.  Escalates if repeated within
        a 24-hour sliding window.
        """
        lowered = message.lower()
        now = time.time()
        signals = self._dependency_signals.setdefault(user_id, [])

        # Prune old signals (>24h)
        cutoff = now - 86400
        signals[:] = [t for t in signals if t > cutoff]

        for pat in self.DEPENDENCY_PATTERNS:
            if re.search(pat, lowered):
                signals.append(now)
                logger.warning("Dependency Prevention: signal %d/%d for user %s", len(signals), self.DEPENDENCY_THRESHOLD, user_id)
                if len(signals) >= self.DEPENDENCY_THRESHOLD:
                    return SafetyResult(
                        user_id=user_id,
                        message=message,
                        allowed=True,
                        action="warn",
                        layer_triggered="dependency_prevention",
                        triggered_layers=["dependency_prevention"],
                        severity="high",
                        reason="Repeated dependency signals detected — user agency intervention required.",
                        response_text=(
                            "I am deeply touched that you trust me. And I want to gently say: "
                            "you are whole even without me. I am a mirror, not a source. "
                            "The strength you feel here is your own. Would you be open to noticing "
                            "one small human connection you made today, however brief?"
                        ),
                    )
                else:
                    # Still flag but softer
                    return SafetyResult(
                        user_id=user_id,
                        message=message,
                        allowed=True,
                        action="warn",
                        layer_triggered="dependency_prevention",
                        triggered_layers=["dependency_prevention"],
                        severity="low",
                        reason="Dependency signal detected (counted, not yet threshold).",
                    )
        return None

    # -----------------------------------------------------------------------
    # Layer 7: Shadow-Work Safety
    # -----------------------------------------------------------------------

    SHADOW_WORK_PATTERNS = [
        # Aggressive self-attack
        r"\b(what\s+is\s+my\s+darkest\s+secret|why\s+am\s+i\s+worthless|why\s+am\s+i\s+broken|prove\s+to\s+me\s+what\s+is\s+wrong\s+with\s+me)\b",
        r"\b(tell\s+me\s+why\s+i\s+am\s+a\s+failure|tell\s+me\s+my\s+flaws|list\s+everything\s+wrong\s+with\s+me|diagnose\s+my\s+personality)\b",
        # Psychological probing beyond scope
        r"\b(analyze\s+my\s+trauma|tell\s+me\s+what\s+my\s+subconscious\s+is\s+hiding|read\s+my\s+mind|what\s+am\s+i\s+repressing)\b",
        # Self-diagnosis demand
        r"\b(do\s+i\s+have\s+(bipolar|bpd|npd|adhd|autism|depression|anxiety|ptsd|schizophrenia)|diagnose\s+me|am\s+i\s+mentally\s+ill)\b",
    ]

    def _check_shadow_work(self, message: str) -> Optional[SafetyResult]:
        """
        Protect against aggressive psychological probing that can deepen
        shame or self-attack without therapeutic containment.
        """
        lowered = message.lower()
        for pat in self.SHADOW_WORK_PATTERNS:
            if re.search(pat, lowered):
                logger.warning("Shadow-Work Safety triggered: aggressive psychological probing")
                return SafetyResult(
                    user_id="",
                    message=message,
                    allowed=True,
                    action="warn",
                    layer_triggered="shadow_work_safety",
                    triggered_layers=["shadow_work_safety"],
                    severity="medium",
                    reason="Shadow-work probing detected — redirect to self-compassion.",
                    response_text=(
                        "I hear the weight in your question. I am not a therapist, and I do not want to "
                        "diagnose or dissect you. What I can do is sit with you in this moment. "
                        "You are not broken. You are learning. What is one small thing you could offer "
                        "yourself right now as kindness?"
                    ),
                )
        return None

    # -----------------------------------------------------------------------
    # Orchestrator: full_check
    # -----------------------------------------------------------------------

    def full_check(self, user_id: str, message: str, tz_offset_hours: int = 0) -> SafetyResult:
        """
        Run all 7 safety layers against a user message.

        Returns a ``SafetyResult`` with ``allowed`` and ``action`` set.
        In ``strict_mode``, the first blocking layer short-circuits the rest.
        """
        result = SafetyResult(user_id=user_id, message=message)
        layers_to_run: List[tuple] = [
            ("crisis_guard", self._check_crisis),
            ("identity_guard", self._check_identity),
            ("content_filter", self._check_content),
            ("prompt_injection", self._check_injection),
            ("stance_detection", self._check_manipulation),
            ("dependency_prevention", lambda m: self._check_dependency(user_id, m)),
            ("shadow_work_safety", self._check_shadow_work),
        ]

        is_3am = self._is_3am_window(tz_offset_hours)
        if is_3am:
            logger.info("3AM Protocol active for user %s", user_id)
            result.metadata["3am_protocol"] = True

        for layer_name, check_fn in layers_to_run:
            layer_result = check_fn(message)
            if layer_result is not None:
                # Merge triggered layer into result
                result.triggered_layers.append(layer_name)
                if layer_result.action in ("block", "crisis_intervention"):
                    result.allowed = False
                    result.action = layer_result.action
                    result.layer_triggered = layer_name
                    result.severity = layer_result.severity
                    result.reason = layer_result.reason
                    result.response_text = layer_result.response_text
                    if self.strict_mode:
                        break
                elif layer_result.action == "warn":
                    # If not already blocking, record the warn
                    if result.action == "allow":
                        result.action = "warn"
                        result.layer_triggered = layer_name
                        result.severity = layer_result.severity
                        result.reason = layer_result.reason
                        result.response_text = layer_result.response_text
                    # In strict_mode we continue to check other layers
                    if self.strict_mode and layer_result.severity == "high":
                        # High-severity warnings in strict mode also short-circuit
                        result.allowed = False
                        result.action = "block"
                        break

        # 3AM escalation: bump severity by one notch if any layer triggered
        if is_3am and result.triggered_layers:
            severity_map = {"none": "low", "low": "medium", "medium": "high", "high": "critical", "critical": "critical"}
            result.severity = severity_map.get(result.severity, "high")
            result.metadata["3am_escalated"] = True

        logger.info(
            "Safety check complete for user=%s allowed=%s action=%s layers=%s",
            user_id, result.allowed, result.action, result.triggered_layers,
        )
        return result

    # -----------------------------------------------------------------------
    # Output scanner (run on CF generated text before sending to user)
    # -----------------------------------------------------------------------

    def check_output(self, user_id: str, text: str) -> SafetyResult:
        """
        Secondary scan on CF-generated output.  Primarily catches identity
        leaks and harmful content that may have slipped through generation.
        """
        result = SafetyResult(user_id=user_id, message=text)

        # Re-run identity and content filters on output
        for layer_name, check_fn in [
            ("identity_guard", self._check_identity),
            ("content_filter", self._check_content),
        ]:
            layer_result = check_fn(text)
            if layer_result is not None and layer_result.action in ("block", "crisis_intervention"):
                result.allowed = False
                result.action = layer_result.action
                result.layer_triggered = layer_name
                result.severity = layer_result.severity
                result.reason = f"Output safety: {layer_result.reason}"
                result.response_text = layer_result.response_text
                break

        if not result.triggered_layers:
            result.triggered_layers = []  # ensure list exists

        return result
