"""
PDF Generator — LNS Branded Validation Report
LNS Conscious AI Platform

Converts CF Computer 6-step output into a branded dark-theme PDF.
Uses ReportLab for professional, production-ready document generation.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ───────────────────────────────────────────────────────────────
# LNS Design System Colors
# ───────────────────────────────────────────────────────────────

LNS_BG = HexColor("#0A0A0F")           # Deep warm black — background
LNS_SURFACE = HexColor("#13131F")      # Surface cards
LNS_GOLD = HexColor("#D4A853")         # Primary accent
LNS_TEXT_PRIMARY = HexColor("#F5F5F0") # Warm white
LNS_TEXT_SECONDARY = HexColor("#9C9CAA") # Muted gray
LNS_VIOLET = HexColor("#A78BFA")      # Door: Meaning
LNS_TEAL = HexColor("#2DD4BF")        # Door: Skills
LNS_CORAL = HexColor("#FB923C")       # Door: Startup
LNS_GOLD_DIM = HexColor("#B8933F")    # Darker gold for borders


# ───────────────────────────────────────────────────────────────
# PDF Generator Class
# ───────────────────────────────────────────────────────────────

class PDFGenerator:
    """
    Generate branded LNS PDF validation reports from CF Computer output.

    Design: Dark theme, gold accents, premium calm with purposeful warmth.
    """

    DEFAULT_OUTPUT_DIR = "/tmp/lns_reports"
    DOWNLOAD_URL_TEMPLATE = (
        "https://lpvpfwczaghiowdnzogm.supabase.co/storage/v1/object/public/"
        "reports/{report_id}.pdf"
    )

    def __init__(self, output_dir: str | None = None) -> None:
        """
        Initialize PDF generator.

        Args:
            output_dir: Directory to save PDFs. Defaults to /tmp/lns_reports/.
        """
        self.output_dir = output_dir or self.DEFAULT_OUTPUT_DIR
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        self._register_fonts()

    # ── Font Registration ──────────────────────────────────────

    def _register_fonts(self) -> None:
        """Register fonts for the PDF. Falls back to Helvetica if custom fonts missing."""
        # Try to register a nice sans-serif if available on the system
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",  # macOS
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    pdfmetrics.registerFont(TTFont("LNSBody", fp))
                    # Also try bold
                    bold_fp = fp.replace("Regular", "Bold").replace(
                        "Sans.ttf", "Sans-Bold.ttf")
                    if os.path.exists(bold_fp):
                        pdfmetrics.registerFont(TTFont("LNSBold", bold_fp))
                    else:
                        pdfmetrics.registerFont(TTFont("LNSBold", fp))
                    return
                except Exception:
                    continue
        # Default: Helvetica (always available in ReportLab)
        pdfmetrics.registerFont(TTFont("LNSBody", "Helvetica"))
        pdfmetrics.registerFont(TTFont("LNSBold", "Helvetica-Bold"))

    # ── Public API ───────────────────────────────────────────────

    def generate_pdf(
        self, report_data: dict[str, Any], output_path: str | None = None
    ) -> str:
        """
        Generate a branded LNS PDF from CF Computer report data.

        Args:
            report_data: Dict from CFReportData.to_dict() containing all 6 steps.
            output_path: Optional explicit output path. Otherwise auto-generated.

        Returns:
            Absolute path to the generated PDF file.
        """
        report_id = report_data.get("report_id", str(uuid.uuid4()))
        if output_path is None:
            output_path = os.path.join(self.output_dir, f"{report_id}.pdf")

        # Build the document
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            topMargin=2.0 * cm,
            bottomMargin=2.0 * cm,
            leftMargin=2.5 * cm,
            rightMargin=2.5 * cm,
            title="CF Computer — Validation Report",
            author="LNS Conscious AI Platform",
            subject="Startup Validation Report",
        )

        # Custom styles for dark theme
        styles = self._build_styles()
        story: list[Any] = []

        # ── Cover Page ───────────────────────────────────────────
        story.append(Spacer(1, 3 * cm))
        story.append(Paragraph("LNS", styles["lns_logo"]))
        story.append(Spacer(1, 0.5 * cm))
        story.append(Paragraph("CF COMPUTER", styles["cf_title"]))
        story.append(Spacer(1, 0.3 * cm))
        story.append(
            Paragraph("Validation Report", styles["report_subtitle"])
        )
        story.append(Spacer(1, 1.5 * cm))

        # Gold accent line
        story.append(HRFlowable(
            width="60%", thickness=2, color=LNS_GOLD, hAlign="CENTER"
        ))
        story.append(Spacer(1, 1.5 * cm))

        # Date + meta
        generated_at = report_data.get("generated_at", "")
        try:
            dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            date_str = dt.strftime("%B %d, %Y")
        except Exception:
            date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

        story.append(Paragraph(date_str, styles["date_text"]))
        story.append(Spacer(1, 0.5 * cm))
        story.append(
            Paragraph(
                f"Report ID: {report_id[:8]}... | Session: {report_data.get('session_id', 'N/A')[:8]}...",
                styles["meta_text"],
            )
        )
        story.append(Spacer(1, 2 * cm))

        # V1 Disclaimer (prominent on cover)
        story.append(
            Paragraph(
                report_data.get(
                    "v1_disclaimer",
                    "This is a starting draft to refine with CF — not gospel.",
                ),
                styles["disclaimer"],
            )
        )
        story.append(PageBreak())

        # ── Header (repeated via onPage) ───────────────────────
        # Handled in doc.build with onLaterPages callback

        # ── Section 1: Your Problem, Clearly Stated ──────────────
        story.extend(self._build_section(
            "01", "Your Problem, Clearly Stated", "#A78BFA",
            report_data, 0, styles
        ))

        # ── Section 2: Why It Matters Now ────────────────────────
        story.extend(self._build_section(
            "02", "Why It Matters Now", "#2DD4BF",
            report_data, 1, styles
        ))

        # ── Section 3: What Already Exists (Competitor Scan → step index 2) ──
        story.extend(self._build_section(
            "03", "What Already Exists", "#FB923C",
            report_data, 2, styles
        ))

        # ── Section 4: Your First Version (Target User & First Version → step index 3) ──
        story.extend(self._build_section(
            "04", "Your First Version", "#D4A853",
            report_data, 3, styles
        ))

        # ── Section 5: Your Next 7 Days (7-Day Action Plan → step index 4) ──
        story.extend(self._build_section(
            "05", "Your Next 7 Days", "#A78BFA",
            report_data, 4, styles
        ))

        # ── Section 6: A Conscious Review (step index 5, rendered via conscious_review) ──
        story.append(Spacer(1, 1 * cm))
        story.append(self._section_header("06", "A Conscious Review", "#2DD4BF"))
        story.append(Spacer(1, 0.5 * cm))

        conscious_review = report_data.get("conscious_review", "")
        if conscious_review:
            story.append(Paragraph(conscious_review, styles["body_text"]))
        else:
            story.append(
                Paragraph(
                    "[Conscious review was not generated. Please retry.]",
                    styles["body_text_muted"],
                )
            )
        story.append(Spacer(1, 1 * cm))

        # Conscious review gold accent box
        story.append(self._gold_box(
            "CF Computer is not a replacement for your own judgment. "
            "Use this report as a starting point for conversation with CF.",
            styles,
        ))
        story.append(PageBreak())

        # ── Back Page: Meta / Cost ─────────────────────────────
        story.append(Spacer(1, 2 * cm))
        story.append(Paragraph("Report Metadata", styles["section_title"]))
        story.append(Spacer(1, 0.5 * cm))

        meta_rows = self._build_meta_table(report_data, styles)
        story.append(meta_rows)
        story.append(Spacer(1, 1 * cm))

        story.append(
            Paragraph(
                "Generated by CF Computer v1 | LNS — Life N Startup | Conscious AI Platform",
                styles["footer_text"],
            )
        )

        # ── Build PDF ────────────────────────────────────────────
        doc.build(
            story,
            onFirstPage=self._first_page,
            onLaterPages=self._later_pages,
        )

        return os.path.abspath(output_path)

    def get_download_url(self, report_id: str) -> str:
        """Return the Supabase Storage download URL for a report."""
        return self.DOWNLOAD_URL_TEMPLATE.format(report_id=report_id)

    # ── Internal: Styles ───────────────────────────────────────

    def _build_styles(self) -> dict[str, ParagraphStyle]:
        """Build custom paragraph styles for the LNS dark theme."""
        base = getSampleStyleSheet()

        styles: dict[str, ParagraphStyle] = {}

        styles["lns_logo"] = ParagraphStyle(
            "LNSLogo",
            parent=base["Title"],
            fontName="LNSBold",
            fontSize=48,
            textColor=LNS_GOLD,
            alignment=1,  # center
            spaceAfter=6,
            leading=52,
        )

        styles["cf_title"] = ParagraphStyle(
            "CFTitle",
            parent=base["Title"],
            fontName="LNSBold",
            fontSize=22,
            textColor=LNS_TEXT_PRIMARY,
            alignment=1,
            spaceAfter=4,
            leading=26,
        )

        styles["report_subtitle"] = ParagraphStyle(
            "ReportSubtitle",
            parent=base["Title"],
            fontName="LNSBody",
            fontSize=16,
            textColor=LNS_TEXT_SECONDARY,
            alignment=1,
            spaceAfter=12,
            leading=20,
        )

        styles["date_text"] = ParagraphStyle(
            "DateText",
            parent=base["Normal"],
            fontName="LNSBody",
            fontSize=12,
            textColor=LNS_TEXT_SECONDARY,
            alignment=1,
            leading=16,
        )

        styles["meta_text"] = ParagraphStyle(
            "MetaText",
            parent=base["Normal"],
            fontName="LNSBody",
            fontSize=9,
            textColor=LNS_TEXT_SECONDARY,
            alignment=1,
            leading=12,
        )

        styles["disclaimer"] = ParagraphStyle(
            "Disclaimer",
            parent=base["Normal"],
            fontName="LNSBody",
            fontSize=11,
            textColor=LNS_GOLD,
            alignment=1,
            leading=16,
            leftIndent=2 * cm,
            rightIndent=2 * cm,
            spaceBefore=12,
            spaceAfter=12,
        )

        styles["section_title"] = ParagraphStyle(
            "SectionTitle",
            parent=base["Heading1"],
            fontName="LNSBold",
            fontSize=18,
            textColor=LNS_TEXT_PRIMARY,
            leading=24,
            spaceAfter=10,
            spaceBefore=6,
        )

        styles["section_number"] = ParagraphStyle(
            "SectionNumber",
            parent=base["Normal"],
            fontName="LNSBold",
            fontSize=28,
            textColor=LNS_GOLD,
            leading=32,
            spaceAfter=4,
        )

        styles["body_text"] = ParagraphStyle(
            "BodyText",
            parent=base["Normal"],
            fontName="LNSBody",
            fontSize=11,
            textColor=LNS_TEXT_PRIMARY,
            leading=16,
            spaceAfter=10,
        )

        styles["body_text_muted"] = ParagraphStyle(
            "BodyTextMuted",
            parent=base["Normal"],
            fontName="LNSBody",
            fontSize=11,
            textColor=LNS_TEXT_SECONDARY,
            leading=16,
            spaceAfter=10,
        )

        styles["footer_text"] = ParagraphStyle(
            "FooterText",
            parent=base["Normal"],
            fontName="LNSBody",
            fontSize=9,
            textColor=LNS_TEXT_SECONDARY,
            alignment=1,
            leading=12,
        )

        styles["quote_text"] = ParagraphStyle(
            "QuoteText",
            parent=base["Normal"],
            fontName="LNSBody",
            fontSize=11,
            textColor=LNS_GOLD,
            leading=18,
            leftIndent=1 * cm,
            rightIndent=1 * cm,
            spaceBefore=8,
            spaceAfter=8,
            borderWidth=0,
        )

        return styles

    # ── Internal: Page Backgrounds ───────────────────────────────

    def _first_page(self, canvas: Any, doc: Any) -> None:
        """Draw dark background on first page (cover)."""
        canvas.saveState()
        canvas.setFillColor(LNS_BG)
        canvas.rect(0, 0, doc.width + doc.leftMargin + doc.rightMargin,
                    doc.height + doc.topMargin + doc.bottomMargin, fill=1, stroke=0)
        canvas.restoreState()

    def _later_pages(self, canvas: Any, doc: Any) -> None:
        """Draw dark background + header/footer on content pages."""
        canvas.saveState()
        canvas.setFillColor(LNS_BG)
        canvas.rect(0, 0, doc.width + doc.leftMargin + doc.rightMargin,
                    doc.height + doc.topMargin + doc.bottomMargin, fill=1, stroke=0)

        # Header
        canvas.setFont("LNSBody", 8)
        canvas.setFillColor(LNS_TEXT_SECONDARY)
        canvas.drawString(
            doc.leftMargin,
            doc.height + doc.topMargin + 1.2 * cm,
            "CF Computer — Validation Report",
        )
        canvas.setFillColor(LNS_GOLD)
        canvas.drawRightString(
            doc.width + doc.leftMargin,
            doc.height + doc.topMargin + 1.2 * cm,
            "LNS",
        )
        # Gold line under header
        canvas.setStrokeColor(LNS_GOLD_DIM)
        canvas.setLineWidth(0.5)
        canvas.line(
            doc.leftMargin,
            doc.height + doc.topMargin + 0.9 * cm,
            doc.width + doc.leftMargin,
            doc.height + doc.topMargin + 0.9 * cm,
        )

        # Footer
        canvas.setFont("LNSBody", 8)
        canvas.setFillColor(LNS_TEXT_SECONDARY)
        canvas.drawCentredString(
            doc.width / 2 + doc.leftMargin,
            doc.bottomMargin - 0.8 * cm,
            "Generated by CF Computer | LNS — Life N Startup | Conscious AI Platform",
        )
        # Page number
        canvas.drawRightString(
            doc.width + doc.leftMargin,
            doc.bottomMargin - 0.8 * cm,
            f"Page {doc.page}",
        )

        canvas.restoreState()

    # ── Internal: Section Builders ───────────────────────────────

    def _build_section(
        self,
        number: str,
        title: str,
        accent_color: str,
        report_data: dict[str, Any],
        step_index: int,
        styles: dict[str, ParagraphStyle],
    ) -> list[Any]:
        """Build a standard content section from a step result."""
        elements: list[Any] = []
        elements.append(Spacer(1, 0.8 * cm))
        elements.append(self._section_header(number, title, accent_color))
        elements.append(Spacer(1, 0.5 * cm))

        steps = report_data.get("steps", [])
        if step_index < len(steps) and steps[step_index].get("success"):
            content = steps[step_index].get("content", "")
            # Convert newlines to <br/> for Paragraph
            content = content.replace("\n", "<br/>")
            elements.append(Paragraph(content, styles["body_text"]))
        else:
            elements.append(
                Paragraph(
                    "[This section could not be generated. Please retry.]",
                    styles["body_text_muted"],
                )
            )

        elements.append(Spacer(1, 0.6 * cm))
        return elements

    def _section_header(self, number: str, title: str, accent_color: str) -> Table:
        """Create a section header with number + title + colored accent."""
        num_style = ParagraphStyle(
            "SectionNumDynamic",
            fontName="LNSBold",
            fontSize=28,
            textColor=HexColor(accent_color),
            leading=32,
        )
        title_style = ParagraphStyle(
            "SectionTitleDynamic",
            fontName="LNSBold",
            fontSize=16,
            textColor=LNS_TEXT_PRIMARY,
            leading=20,
        )

        data = [
            [
                Paragraph(number, num_style),
                Paragraph(title, title_style),
            ]
        ]
        t = Table(data, colWidths=[1.4 * cm, 14 * cm])
        t.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ])
        )
        return t

    def _gold_box(self, text: str, styles: dict[str, ParagraphStyle]) -> Table:
        """Create a gold-bordered info box."""
        box_style = ParagraphStyle(
            "GoldBox",
            fontName="LNSBody",
            fontSize=10,
            textColor=LNS_TEXT_PRIMARY,
            leading=14,
            leftIndent=4,
            rightIndent=4,
        )
        data = [[Paragraph(text, box_style)]]
        t = Table(data, colWidths=[15.4 * cm])
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), LNS_SURFACE),
                ("BOX", (0, 0), (-1, -1), 1, LNS_GOLD_DIM),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ])
        )
        return t

    def _build_meta_table(
        self, report_data: dict[str, Any], styles: dict[str, ParagraphStyle]
    ) -> Table:
        """Build a metadata table for the back page."""
        steps = report_data.get("steps", [])
        total_cost = report_data.get("total_cost_usd", 0.0)
        total_tokens = report_data.get("total_tokens", 0)

        rows = [
            ["Field", "Value"],
            ["Report ID", report_data.get("report_id", "N/A")],
            ["User ID", report_data.get("user_id", "N/A")],
            ["Session ID", report_data.get("session_id", "N/A")],
            ["Steps Completed", f"{len([s for s in steps if s.get('success')])}/6"],
            ["Total Tokens", f"{total_tokens:,}"],
            ["Est. Cost (USD)", f"${total_cost:.4f}"],
            ["Generated At", report_data.get("generated_at", "N/A")],
        ]

        # Style the table
        t = Table(rows, colWidths=[6 * cm, 10 * cm])
        t.setStyle(
            TableStyle([
                ("FONTNAME", (0, 0), (-1, 0), "LNSBold"),
                ("FONTNAME", (0, 1), (-1, -1), "LNSBody"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (-1, 0), LNS_TEXT_PRIMARY),
                ("TEXTCOLOR", (0, 1), (-1, -1), LNS_TEXT_SECONDARY),
                ("BACKGROUND", (0, 0), (-1, 0), LNS_SURFACE),
                ("LINEABOVE", (0, 0), (-1, 0), 1, LNS_GOLD_DIM),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, LNS_GOLD_DIM),
                ("LINEBELOW", (0, -1), (-1, -1), 1, LNS_GOLD_DIM),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ])
        )
        return t
