#!/usr/bin/env python3
"""
email_automation.py
===================
Email automation system for the LNS Conscious AI Platform.

Handles the 3-email welcome sequence for waitlist signups:
1. Welcome Email (Day 0 — within 5 minutes)
2. Founder Note (Day 3)
3. Cohort Confirmation (Day 7)

Designed for FastAPI integration or Supabase Edge Functions.
Supports Resend API (SendGrid-compatible structure available).

Author: LNS Engineering
Version: 1.0.0
"""

import os
import uuid
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

from jinja2 import Environment, BaseLoader, Template

# ───────────────────────────────────────────────────────────────────────────────
# CONFIGURATION — loaded from environment
# ───────────────────────────────────────────────────────────────────────────────

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "LNS — Life N Startup")
EMAIL_FROM_ADDRESS = os.environ.get("EMAIL_FROM_ADDRESS", "hello@lns.life")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO", "nitin@lns.life")
BETA_ACCESS_URL = os.environ.get("BETA_ACCESS_URL", "https://lns.life/beta")
TELEGRAM_COMMUNITY_URL = os.environ.get("TELEGRAM_COMMUNITY_URL", "https://t.me/lnsconsciousbuilders")

# ───────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ───────────────────────────────────────────────────────────────────────────────

@dataclass
class EmailUser:
    """Represents a waitlist user for email automation."""
    email: str
    name: str
    waitlist_type: str = "student"  # 'student' or 'university'
    university_name: Optional[str] = None
    cohort: str = "beta_1"
    user_id: Optional[str] = None
    unsubscribe_token: str = field(default_factory=lambda: str(uuid.uuid4()))

@dataclass
class EmailDelivery:
    """Tracks the delivery status of a single email."""
    delivery_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    email_type: str = ""  # 'welcome', 'founder_note', 'cohort_confirm'
    status: str = "pending"  # 'pending', 'sent', 'delivered', 'opened', 'clicked', 'bounced', 'failed'
    sent_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    clicked_at: Optional[datetime] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

# ───────────────────────────────────────────────────────────────────────────────
# LOGGING SETUP
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("email_automation")

# ───────────────────────────────────────────────────────────────────────────────
# JINJA2 EMAIL TEMPLATES — HTML + PLAIN TEXT
# ───────────────────────────────────────────────────────────────────────────────

# ─── WELCOME EMAIL TEMPLATES ───────────────────────────────────────────────────

WELCOME_EMAIL_SUBJECT = "Welcome to the Conscious Builders, {{ name }}"

WELCOME_EMAIL_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Welcome to the Conscious Builders</title>
<style>
  body { margin: 0; padding: 0; background-color: #0A0A0F; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #F5F5F0; }
  .container { max-width: 600px; margin: 0 auto; padding: 40px 24px; }
  .brand { text-align: center; margin-bottom: 32px; }
  .brand-circle { width: 56px; height: 56px; border-radius: 50%; background: #D4A853; display: inline-block; margin-bottom: 12px; }
  .brand-text { font-size: 12px; letter-spacing: 3px; text-transform: uppercase; color: #D4A853; }
  h1 { font-size: 28px; font-weight: 400; color: #F5F5F0; margin: 24px 0 16px; line-height: 1.3; }
  .accent { color: #D4A853; }
  p { font-size: 16px; line-height: 1.7; color: #C8C8C0; margin: 16px 0; }
  .highlight-box { background: rgba(212, 168, 83, 0.08); border-left: 3px solid #D4A853; padding: 20px 24px; margin: 24px 0; border-radius: 0 8px 8px 0; }
  .highlight-box p { margin: 0; color: #F5F5F0; }
  .cta { display: inline-block; background: #D4A853; color: #0A0A0F; text-decoration: none; padding: 14px 28px; border-radius: 8px; font-weight: 600; font-size: 15px; margin: 24px 0; }
  .divider { height: 1px; background: rgba(212, 168, 83, 0.2); margin: 32px 0; }
  .footer { font-size: 13px; color: #888; text-align: center; margin-top: 40px; }
  .footer a { color: #D4A853; text-decoration: none; }
</style>
</head>
<body>
<div class="container">
  <div class="brand">
    <div class="brand-circle"></div>
    <div class="brand-text">LNS — Life N Startup</div>
  </div>

  <h1>You're in. <span class="accent">Welcome</span> to the conscious builders.</h1>

  <p>Hi {{ name }},</p>

  <p>I'm writing this just a few minutes after you joined. That means you're one of the first people who believed this was worth building. Thank you for that.</p>

  <div class="highlight-box">
    <p><strong>CF is not a chatbot.</strong> CF is a companion — someone who sits with you when you're confused, challenges you when you're coasting, and walks beside you while you figure out what you're actually here to build.</p>
  </div>

  <p>Here's what happens next:</p>
  <p>
    • You'll get early access to the beta in the coming weeks<br>
    • I'll personally reach out for a quick onboarding call<br>
    • You'll join a small community of people who care about the same things you do
  </p>

  <p>Until then, you can meet CF at <a href="https://lns.life" style="color:#D4A853;">lns.life</a> — say hello, if you want.</p>

  <a href="https://lns.life" class="cta">Meet CF at lns.life</a>

  <div class="divider"></div>

  <p style="font-size:14px; color:#A0A0A0;">With warmth,<br><strong>Nitin</strong><br>Founder, LNS</p>

  <div class="footer">
    <p>You joined the waitlist on {{ signup_date }}.<br>
    <a href="{{ unsubscribe_url }}">Unsubscribe</a> — no hard feelings, ever.</p>
  </div>
</div>
</body>
</html>
"""

WELCOME_EMAIL_TEXT = """
Hi {{ name }},

You're in. Welcome to the conscious builders.

I'm writing this just a few minutes after you joined. That means you're one of the first people who believed this was worth building. Thank you for that.

CF is not a chatbot. CF is a companion — someone who sits with you when you're confused, challenges you when you're coasting, and walks beside you while you figure out what you're actually here to build.

Here's what happens next:
- You'll get early access to the beta in the coming weeks
- I'll personally reach out for a quick onboarding call
- You'll join a small community of people who care about the same things you do

Until then, you can meet CF at lns.life — say hello, if you want.

→ Meet CF: https://lns.life

—
With warmth,
Nitin
Founder, LNS

---
You joined the waitlist on {{ signup_date }}.
Unsubscribe: {{ unsubscribe_url }}
"""

# ─── FOUNDER NOTE TEMPLATES ───────────────────────────────────────────────────

FOUNDER_NOTE_SUBJECT = "Why I built this — a note from Nitin"

FOUNDER_NOTE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Why I built this</title>
<style>
  body { margin: 0; padding: 0; background-color: #0A0A0F; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #F5F5F0; }
  .container { max-width: 600px; margin: 0 auto; padding: 40px 24px; }
  .brand { text-align: center; margin-bottom: 32px; }
  .brand-text { font-size: 12px; letter-spacing: 3px; text-transform: uppercase; color: #D4A853; }
  h1 { font-size: 26px; font-weight: 400; color: #F5F5F0; margin: 24px 0 16px; line-height: 1.3; }
  .accent { color: #D4A853; }
  p { font-size: 16px; line-height: 1.8; color: #C8C8C0; margin: 18px 0; }
  .quote { border-left: 3px solid #D4A853; padding-left: 20px; margin: 24px 0; font-style: italic; color: #E8E8E0; }
  .cta { display: inline-block; border: 1px solid #D4A853; color: #D4A853; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-size: 14px; margin: 24px 0; }
  .divider { height: 1px; background: rgba(212, 168, 83, 0.2); margin: 32px 0; }
  .footer { font-size: 13px; color: #888; text-align: center; margin-top: 40px; }
  .footer a { color: #D4A853; text-decoration: none; }
</style>
</head>
<body>
<div class="container">
  <div class="brand">
    <div class="brand-text">LNS — Life N Startup</div>
  </div>

  <h1>Why I built this — <span class="accent">a note from Nitin</span></h1>

  <p>Hi {{ name }},</p>

  <p>Three years ago, I was sitting in a government office in Dubai. Good salary. Stable visa. Everyone thought I had it figured out.</p>

  <p>But I felt hollow. Like I was performing a version of myself that wasn't mine.</p>

  <div class="quote">
    I kept asking: What if the thing I'm actually here to do is something I haven't even tried yet?
  </div>

  <p>I left that job. I started teaching. I worked with students in India who were brilliant, anxious, and deeply uncertain — not because they lacked talent, but because they had never been asked what actually matters to them.</p>

  <p>That's when LNS started. Not as a product. As a conversation.</p>

  <p>I'm building this for Gen Z Indians who feel the same pressure I felt — the pressure to be impressive, to have a plan, to not "waste time" — but who also feel something pulling at them. Something that says, <em>there's more.</em></p>

  <p>CF is that pull, made visible. CF Computer is the friend who actually helps you build it, not just dream about it.</p>

  <p>The beta is small. Intentional. The first 3,000 of us. We'll figure it out together.</p>

  <a href="mailto:nitin@lns.life" class="cta">Reply to this email — I read every one</a>

  <div class="divider"></div>

  <p style="font-size:14px; color:#A0A0A0;">With everything I've got,<br><strong>Nitin</strong><br>Founder, LNS</p>

  <div class="footer">
    <p><a href="{{ unsubscribe_url }}">Unsubscribe</a> — no hard feelings, ever.</p>
  </div>
</div>
</body>
</html>
"""

FOUNDER_NOTE_TEXT = """
Hi {{ name }},

Why I built this — a note from Nitin

Three years ago, I was sitting in a government office in Dubai. Good salary. Stable visa. Everyone thought I had it figured out.

But I felt hollow. Like I was performing a version of myself that wasn't mine.

I kept asking: What if the thing I'm actually here to do is something I haven't even tried yet?

I left that job. I started teaching. I worked with students in India who were brilliant, anxious, and deeply uncertain — not because they lacked talent, but because they had never been asked what actually matters to them.

That's when LNS started. Not as a product. As a conversation.

I'm building this for Gen Z Indians who feel the same pressure I felt — the pressure to be impressive, to have a plan, to not "waste time" — but who also feel something pulling at them. Something that says, there's more.

CF is that pull, made visible. CF Computer is the friend who actually helps you build it, not just dream about it.

The beta is small. Intentional. The first 3,000 of us. We'll figure it out together.

→ Reply to this email — I read every one.

—
With everything I've got,
Nitin
Founder, LNS

---
Unsubscribe: {{ unsubscribe_url }}
"""

# ─── COHORT CONFIRMATION TEMPLATES ────────────────────────────────────────────

COHORT_CONFIRM_SUBJECT = "You're in the first cohort. Here's what to expect."

COHORT_CONFIRM_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>You're in the first cohort</title>
<style>
  body { margin: 0; padding: 0; background-color: #0A0A0F; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #F5F5F0; }
  .container { max-width: 600px; margin: 0 auto; padding: 40px 24px; }
  .brand { text-align: center; margin-bottom: 32px; }
  .brand-text { font-size: 12px; letter-spacing: 3px; text-transform: uppercase; color: #D4A853; }
  h1 { font-size: 26px; font-weight: 400; color: #F5F5F0; margin: 24px 0 16px; line-height: 1.3; }
  .accent { color: #D4A853; }
  p { font-size: 16px; line-height: 1.7; color: #C8C8C0; margin: 16px 0; }
  .highlight-box { background: rgba(212, 168, 83, 0.08); border-left: 3px solid #D4A853; padding: 20px 24px; margin: 24px 0; border-radius: 0 8px 8px 0; }
  .highlight-box p { margin: 0; color: #F5F5F0; }
  .timeline { margin: 24px 0; }
  .timeline-item { display: flex; margin: 16px 0; }
  .timeline-dot { width: 10px; height: 10px; border-radius: 50%; background: #D4A853; margin-top: 6px; margin-right: 16px; flex-shrink: 0; }
  .timeline-text { font-size: 15px; color: #C8C8C0; line-height: 1.6; }
  .timeline-text strong { color: #F5F5F0; }
  .cta { display: inline-block; background: #D4A853; color: #0A0A0F; text-decoration: none; padding: 14px 28px; border-radius: 8px; font-weight: 600; font-size: 15px; margin: 24px 0; }
  .cta-secondary { display: inline-block; border: 1px solid #D4A853; color: #D4A853; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-size: 14px; margin: 8px 0; }
  .divider { height: 1px; background: rgba(212, 168, 83, 0.2); margin: 32px 0; }
  .footer { font-size: 13px; color: #888; text-align: center; margin-top: 40px; }
  .footer a { color: #D4A853; text-decoration: none; }
</style>
</head>
<body>
<div class="container">
  <div class="brand">
    <div class="brand-text">LNS — Life N Startup</div>
  </div>

  <h1>You're officially in the <span class="accent">first cohort</span> of 3,000 conscious builders.</h1>

  <p>Hi {{ name }},</p>

  <p>This is it. The beta is live, and you're one of the first people in.</p>

  <div class="highlight-box">
    <p><strong>Your access link:</strong> <a href="{{ beta_access_url }}" style="color:#D4A853; word-break:break-all;">{{ beta_access_url }}</a></p>
  </div>

  <p>Here's what you'll get:</p>
  <p>
    • <strong>CF Chat</strong> — your conscious friend, always available<br>
    • <strong>CF Computer</strong> — turns your ideas into validation reports<br>
    • <strong>Your first report</strong> — a clear, honest snapshot of what you're building<br>
    • <strong>A community</strong> — people who actually care about meaning, not just metrics
  </p>

  <div class="timeline">
    <div class="timeline-item">
      <div class="timeline-dot"></div>
      <div class="timeline-text"><strong>Week 1:</strong> Explore CF, generate your first report</div>
    </div>
    <div class="timeline-item">
      <div class="timeline-dot"></div>
      <div class="timeline-text"><strong>Week 2:</strong> Feedback session with the team (optional, 15 min)</div>
    </div>
    <div class="timeline-item">
      <div class="timeline-dot"></div>
      <div class="timeline-text"><strong>Week 3:</strong> New features drop based on what you asked for</div>
    </div>
    <div class="timeline-item">
      <div class="timeline-dot"></div>
      <div class="timeline-text"><strong>After beta:</strong> You keep free access forever as a founding member</div>
    </div>
  </div>

  <a href="{{ beta_access_url }}" class="cta">Access the Beta</a>
  <br>
  <a href="{{ telegram_url }}" class="cta-secondary">Join the Telegram Community</a>

  <div class="divider"></div>

  <p style="font-size:14px; color:#A0A0A0;">
    <strong>Onboarding call:</strong> I'll send you a calendar invite shortly. If you don't see it, check spam or reply to this email.
  </p>

  <p style="font-size:14px; color:#A0A0A0;">With excitement,<br><strong>Nitin</strong><br>Founder, LNS</p>

  <div class="footer">
    <p><a href="{{ unsubscribe_url }}">Unsubscribe</a> — no hard feelings, ever.</p>
  </div>
</div>
</body>
</html>
"""

COHORT_CONFIRM_TEXT = """
Hi {{ name }},

You're officially in the first cohort of 3,000 conscious builders.

This is it. The beta is live, and you're one of the first people in.

Your access link: {{ beta_access_url }}

Here's what you'll get:
• CF Chat — your conscious friend, always available
• CF Computer — turns your ideas into validation reports
• Your first report — a clear, honest snapshot of what you're building
• A community — people who actually care about meaning, not just metrics

Timeline:
Week 1: Explore CF, generate your first report
Week 2: Feedback session with the team (optional, 15 min)
Week 3: New features drop based on what you asked for
After beta: You keep free access forever as a founding member

→ Access the Beta: {{ beta_access_url }}
→ Join the Telegram Community: {{ telegram_url }}

---
Onboarding call: I'll send you a calendar invite shortly. If you don't see it, check spam or reply to this email.

With excitement,
Nitin
Founder, LNS

---
Unsubscribe: {{ unsubscribe_url }}
"""

# ───────────────────────────────────────────────────────────────────────────────
# EMAIL AUTOMATION CLASS
# ───────────────────────────────────────────────────────────────────────────────

class EmailAutomation:
    """
    Email automation engine for LNS waitlist sequences.

    Handles:
    - 3-email welcome sequence scheduling
    - Template rendering (HTML + plain text)
    - Delivery via Resend API (SendGrid-compatible)
    - Delivery tracking and status updates
    - Unsubscribe URL generation (DPDP compliant)

    Usage:
        automation = EmailAutomation()
        await automation.trigger_welcome_sequence("user@email.com", "Rahul", "student")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        from_name: Optional[str] = None,
        from_address: Optional[str] = None,
        reply_to: Optional[str] = None,
        base_url: str = "https://lns.life",
    ):
        self.api_key = api_key or RESEND_API_KEY
        self.from_name = from_name or EMAIL_FROM_NAME
        self.from_address = from_address or EMAIL_FROM_ADDRESS
        self.reply_to = reply_to or EMAIL_REPLY_TO
        self.base_url = base_url
        self.jinja_env = Environment(loader=BaseLoader())
        self._delivery_log: Dict[str, EmailDelivery] = {}
        self._sequence_log: Dict[str, List[str]] = {}  # email -> list of delivery_ids

        if not self.api_key:
            logger.warning("RESEND_API_KEY not set. Emails will be logged but NOT sent.")

    # ── Template Rendering ───────────────────────────────────────────────────

    def _render(self, template_str: str, context: Dict[str, Any]) -> str:
        """Render a Jinja2 template string with the given context."""
        template = self.jinja_env.from_string(template_str)
        return template.render(**context)

    def _get_unsubscribe_url(self, token: str) -> str:
        """Generate a one-click unsubscribe URL."""
        return f"{self.base_url}/unsubscribe?token={token}"

    def _build_context(self, user: EmailUser) -> Dict[str, Any]:
        """Build the shared template context for all emails."""
        return {
            "name": user.name,
            "email": user.email,
            "cohort": user.cohort,
            "signup_date": datetime.now().strftime("%B %d, %Y"),
            "unsubscribe_url": self._get_unsubscribe_url(user.unsubscribe_token),
            "beta_access_url": BETA_ACCESS_URL,
            "telegram_url": TELEGRAM_COMMUNITY_URL,
        }

    # ── Individual Email Builders ────────────────────────────────────────────

    def build_welcome_email(self, user: EmailUser) -> Dict[str, str]:
        """Build the Day 0 Welcome email (HTML + text + subject)."""
        context = self._build_context(user)
        return {
            "subject": self._render(WELCOME_EMAIL_SUBJECT, context),
            "html": self._render(WELCOME_EMAIL_HTML, context),
            "text": self._render(WELCOME_EMAIL_TEXT, context),
        }

    def build_founder_note(self, user: EmailUser) -> Dict[str, str]:
        """Build the Day 3 Founder Note email (HTML + text + subject)."""
        context = self._build_context(user)
        return {
            "subject": self._render(FOUNDER_NOTE_SUBJECT, context),
            "html": self._render(FOUNDER_NOTE_HTML, context),
            "text": self._render(FOUNDER_NOTE_TEXT, context),
        }

    def build_cohort_confirmation(self, user: EmailUser) -> Dict[str, str]:
        """Build the Day 7 Cohort Confirmation email (HTML + text + subject)."""
        context = self._build_context(user)
        return {
            "subject": self._render(COHORT_CONFIRM_SUBJECT, context),
            "html": self._render(COHORT_CONFIRM_HTML, context),
            "text": self._render(COHORT_CONFIRM_TEXT, context),
        }

    # ── Delivery Engine ────────────────────────────────────────────────────────

    async def _send_via_resend(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> EmailDelivery:
        """
        Send an email via the Resend API.

        Returns an EmailDelivery object with status tracking.
        If no API key is configured, logs the email and returns a mock delivery.
        """
        delivery = EmailDelivery(
            email_type=metadata.get("email_type", "unknown") if metadata else "unknown",
            metadata=metadata or {},
        )

        if not self.api_key:
            logger.info(
                f"[MOCK SEND] To: {to_email} | Subject: {subject} | "
                f"Type: {delivery.email_type}"
            )
            delivery.status = "sent"
            delivery.sent_at = datetime.now()
            self._delivery_log[delivery.delivery_id] = delivery
            return delivery

        try:
            import httpx

            payload = {
                "from": f"{self.from_name} <{self.from_address}>",
                "to": [to_email],
                "reply_to": self.reply_to,
                "subject": subject,
                "html": html_body,
                "text": text_body,
            }

            # Resend tags for tracking
            if metadata and metadata.get("email_type"):
                payload["tags"] = [{"name": "email_type", "value": metadata["email_type"]}]

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

            if response.status_code in (200, 202):
                data = response.json()
                delivery.status = "sent"
                delivery.sent_at = datetime.now()
                delivery.metadata["resend_id"] = data.get("id")
                logger.info(f"Email sent to {to_email} via Resend. ID: {data.get('id')}")
            else:
                delivery.status = "failed"
                delivery.error_message = f"Resend API error: {response.status_code} — {response.text}"
                logger.error(delivery.error_message)

        except Exception as e:
            delivery.status = "failed"
            delivery.error_message = str(e)
            logger.error(f"Failed to send email to {to_email}: {e}")

        self._delivery_log[delivery.delivery_id] = delivery
        return delivery

    # ── Public API: Send Single Email ──────────────────────────────────────────

    async def send_email(
        self,
        user: EmailUser,
        email_type: str,
    ) -> EmailDelivery:
        """
        Send a single email of the given type to a user.

        Args:
            user: The EmailUser to send to.
            email_type: One of 'welcome', 'founder_note', 'cohort_confirm'.

        Returns:
            EmailDelivery object with tracking info.
        """
        builders = {
            "welcome": self.build_welcome_email,
            "founder_note": self.build_founder_note,
            "cohort_confirm": self.build_cohort_confirmation,
        }

        builder = builders.get(email_type)
        if not builder:
            raise ValueError(f"Unknown email_type: {email_type}. Use: {list(builders.keys())}")

        email = builder(user)
        delivery = await self._send_via_resend(
            to_email=user.email,
            subject=email["subject"],
            html_body=email["html"],
            text_body=email["text"],
            metadata={
                "email_type": email_type,
                "user_email": user.email,
                "user_name": user.name,
                "cohort": user.cohort,
            },
        )

        # Track in sequence log
        if user.email not in self._sequence_log:
            self._sequence_log[user.email] = []
        self._sequence_log[user.email].append(delivery.delivery_id)

        return delivery

    # ── Public API: Trigger Full Sequence ─────────────────────────────────────

    async def trigger_welcome_sequence(
        self,
        user_email: str,
        user_name: str,
        waitlist_type: str = "student",
        university_name: Optional[str] = None,
        cohort: str = "beta_1",
    ) -> Dict[str, Any]:
        """
        Trigger the complete 3-email welcome sequence for a new waitlist signup.

        Sends:
        1. Welcome email immediately (Day 0, within 5 minutes)
        2. Founder Note on Day 3
        3. Cohort Confirmation on Day 7

        Args:
            user_email: The email address of the new waitlist user.
            user_name: The first name for personalization.
            waitlist_type: 'student' or 'university'.
            university_name: Optional university name for context.
            cohort: The beta cohort identifier (e.g., 'beta_1').

        Returns:
            Dict with sequence_id, scheduled_deliveries, and status.
        """
        user = EmailUser(
            email=user_email,
            name=user_name,
            waitlist_type=waitlist_type,
            university_name=university_name,
            cohort=cohort,
        )

        sequence_id = str(uuid.uuid4())
        scheduled: List[Dict[str, Any]] = []

        # ── Email 1: Welcome (Day 0 — immediate, with 5-minute buffer) ─────
        logger.info(f"[Sequence {sequence_id}] Scheduling welcome email for {user_email}")
        delivery1 = await self.send_email(user, "welcome")
        scheduled.append({
            "email_type": "welcome",
            "scheduled_for": "now (Day 0)",
            "delivery_id": delivery1.delivery_id,
            "status": delivery1.status,
        })

        # ── Email 2: Founder Note (Day 3) ──────────────────────────────────
        founder_send_time = datetime.now() + timedelta(days=3)
        logger.info(
            f"[Sequence {sequence_id}] Scheduling founder note for {user_email} "
            f"at {founder_send_time.isoformat()}"
        )
        scheduled.append({
            "email_type": "founder_note",
            "scheduled_for": founder_send_time.isoformat(),
            "delivery_id": None,  # Will be sent by scheduler / cron
            "status": "scheduled",
        })

        # ── Email 3: Cohort Confirmation (Day 7) ──────────────────────────
        cohort_send_time = datetime.now() + timedelta(days=7)
        logger.info(
            f"[Sequence {sequence_id}] Scheduling cohort confirmation for {user_email} "
            f"at {cohort_send_time.isoformat()}"
        )
        scheduled.append({
            "email_type": "cohort_confirm",
            "scheduled_for": cohort_send_time.isoformat(),
            "delivery_id": None,
            "status": "scheduled",
        })

        return {
            "sequence_id": sequence_id,
            "user_email": user_email,
            "user_name": user_name,
            "cohort": cohort,
            "scheduled_deliveries": scheduled,
            "status": "active",
        }

    # ── Scheduled Delivery Helpers (for cron / job queue) ────────────────────

    async def send_scheduled_email(
        self,
        user_email: str,
        user_name: str,
        email_type: str,
        waitlist_type: str = "student",
        cohort: str = "beta_1",
    ) -> EmailDelivery:
        """
        Send a scheduled email for a user in the welcome sequence.
        Called by the cron job / scheduler on Day 3 and Day 7.
        """
        user = EmailUser(
            email=user_email,
            name=user_name,
            waitlist_type=waitlist_type,
            cohort=cohort,
        )
        return await self.send_email(user, email_type)

    # ── Webhook Handlers ───────────────────────────────────────────────────────

    def handle_delivery_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a delivery status webhook from Resend or SendGrid.

        Args:
            payload: The webhook JSON payload.

        Returns:
            Dict with updated delivery status.
        """
        event_type = payload.get("type", payload.get("event", "unknown"))
        email_id = payload.get("data", {}).get("id", payload.get("email_id"))

        # Map webhook events to internal statuses
        status_map = {
            "email.sent": "sent",
            "email.delivered": "delivered",
            "email.opened": "opened",
            "email.clicked": "clicked",
            "email.bounced": "bounced",
            "email.delivery_failed": "failed",
            "bounce": "bounced",
            "delivered": "delivered",
            "open": "opened",
            "click": "clicked",
        }

        new_status = status_map.get(event_type, "unknown")
        timestamp = datetime.now()

        # Find matching delivery by resend_id or email_id
        delivery: Optional[EmailDelivery] = None
        for d in self._delivery_log.values():
            if d.metadata.get("resend_id") == email_id:
                delivery = d
                break

        if delivery:
            delivery.status = new_status
            if new_status == "opened":
                delivery.opened_at = timestamp
            elif new_status == "clicked":
                delivery.clicked_at = timestamp
            elif new_status == "sent":
                delivery.sent_at = timestamp

            logger.info(f"Webhook updated delivery {delivery.delivery_id} to {new_status}")
            return {
                "delivery_id": delivery.delivery_id,
                "status": new_status,
                "email_type": delivery.email_type,
                "updated_at": timestamp.isoformat(),
            }

        logger.warning(f"Webhook received for unknown email_id: {email_id}")
        return {"status": "unknown", "email_id": email_id, "event": event_type}

    # ── Unsubscribe Handler ────────────────────────────────────────────────────

    def verify_unsubscribe_token(self, token: str, expected_token: str) -> bool:
        """Verify that an unsubscribe token is valid (one-click, DPDP compliant)."""
        return token == expected_token

    # ── Delivery Status Queries ──────────────────────────────────────────────

    def get_delivery_status(self, delivery_id: str) -> Optional[EmailDelivery]:
        """Get the current status of a delivery by ID."""
        return self._delivery_log.get(delivery_id)

    def get_sequence_status(self, email: str) -> Dict[str, Any]:
        """Get the status of all emails in a user's sequence."""
        delivery_ids = self._sequence_log.get(email, [])
        deliveries = [self._delivery_log.get(did) for did in delivery_ids]
        return {
            "email": email,
            "total_scheduled": len(delivery_ids),
            "deliveries": [
                {
                    "delivery_id": d.delivery_id,
                    "email_type": d.email_type,
                    "status": d.status,
                    "sent_at": d.sent_at.isoformat() if d.sent_at else None,
                    "opened_at": d.opened_at.isoformat() if d.opened_at else None,
                    "clicked_at": d.clicked_at.isoformat() if d.clicked_at else None,
                }
                for d in deliveries if d
            ],
        }

    # ── Supabase Integration Helpers ───────────────────────────────────────────

    async def log_to_supabase(
        self,
        delivery: EmailDelivery,
        supabase_client: Any,
    ) -> Dict[str, Any]:
        """
        Log a delivery event to the Supabase email_deliveries table.

        Args:
            delivery: The EmailDelivery object to log.
            supabase_client: A Supabase client instance (e.g., from supabase-py).

        Returns:
            The inserted row data or error info.
        """
        try:
            row = {
                "email_type": delivery.email_type,
                "status": delivery.status,
                "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
                "opened_at": delivery.opened_at.isoformat() if delivery.opened_at else None,
                "clicked_at": delivery.clicked_at.isoformat() if delivery.clicked_at else None,
                "error_message": delivery.error_message,
                "metadata": delivery.metadata,
            }
            result = await supabase_client.table("email_deliveries").insert(row).execute()
            return {"success": True, "data": result.data}
        except Exception as e:
            logger.error(f"Failed to log delivery to Supabase: {e}")
            return {"success": False, "error": str(e)}


# ───────────────────────────────────────────────────────────────────────────────
# SUPABASE EDGE FUNCTION COMPATIBLE WRAPPER
# ───────────────────────────────────────────────────────────────────────────────

async def handle_waitlist_signup_event(
    payload: Dict[str, Any],
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Edge Function entrypoint for Supabase database webhooks.

    Triggered when a new row is inserted into waitlist_entries.
    Automatically starts the welcome sequence.

    Args:
        payload: The Supabase webhook payload (record, type, table, etc.)
        api_key: Optional override for Resend API key.

    Returns:
        Dict with sequence status.
    """
    record = payload.get("record", {})
    email = record.get("email")
    name = record.get("name", "there")
    waitlist_type = record.get("type", "student")
    university_name = record.get("university_name")
    cohort = record.get("cohort", "beta_1")

    if not email:
        return {"error": "No email found in payload", "status": "failed"}

    automation = EmailAutomation(api_key=api_key)
    result = await automation.trigger_welcome_sequence(
        user_email=email,
        user_name=name,
        waitlist_type=waitlist_type,
        university_name=university_name,
        cohort=cohort,
    )
    return result


# ───────────────────────────────────────────────────────────────────────────────
# CLI / STANDALONE TEST
# ───────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    async def _test():
        automation = EmailAutomation()

        # Test welcome sequence (mock mode, no API key)
        result = await automation.trigger_welcome_sequence(
            user_email="test@example.com",
            user_name="Rahul",
            waitlist_type="student",
            cohort="beta_1",
        )
        print("\n=== Welcome Sequence Triggered ===")
        print(f"Sequence ID: {result['sequence_id']}")
        for d in result["scheduled_deliveries"]:
            print(f"  - {d['email_type']}: {d['status']} (scheduled: {d['scheduled_for']})")

        # Test sequence status query
        status = automation.get_sequence_status("test@example.com")
        print("\n=== Sequence Status ===")
        print(f"User: {status['email']}")
        for d in status["deliveries"]:
            print(f"  - {d['email_type']}: {d['status']}")

    asyncio.run(_test())
