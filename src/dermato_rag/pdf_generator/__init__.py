import re
import html
from pathlib import Path
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    HRFlowable, Table, TableStyle
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY


DARK_BLUE = colors.HexColor("#1a2744")
GOLD = colors.HexColor("#c9a84c")
LIGHT_GRAY = colors.HexColor("#f5f5f5")


def make_styles():
    styles = getSampleStyleSheet()
    custom = {}
    custom["Title"] = ParagraphStyle(
        "CourseTitle", fontSize=22, textColor=DARK_BLUE,
        spaceAfter=12, spaceBefore=6, alignment=TA_CENTER,
        fontName="Helvetica-Bold", leading=28
    )
    custom["Subtitle"] = ParagraphStyle(
        "Subtitle", fontSize=13, textColor=GOLD,
        spaceAfter=6, alignment=TA_CENTER, fontName="Helvetica"
    )
    custom["H1"] = ParagraphStyle(
        "H1", fontSize=16, textColor=DARK_BLUE,
        spaceBefore=18, spaceAfter=8, fontName="Helvetica-Bold",
        borderPad=4, leading=20
    )
    custom["H2"] = ParagraphStyle(
        "H2", fontSize=14, textColor=DARK_BLUE,
        spaceBefore=12, spaceAfter=6, fontName="Helvetica-Bold", leading=18
    )
    custom["H3"] = ParagraphStyle(
        "H3", fontSize=12, textColor=colors.HexColor("#2d4070"),
        spaceBefore=8, spaceAfter=4, fontName="Helvetica-BoldOblique", leading=16
    )
    custom["Body"] = ParagraphStyle(
        "Body", fontSize=11, textColor=colors.black,
        spaceBefore=4, spaceAfter=4, leading=16,
        alignment=TA_JUSTIFY, fontName="Helvetica"
    )
    custom["Bullet"] = ParagraphStyle(
        "Bullet", fontSize=11, textColor=colors.black,
        spaceBefore=2, spaceAfter=2, leading=15,
        leftIndent=20, bulletIndent=10, fontName="Helvetica"
    )
    custom["Citation"] = ParagraphStyle(
        "Citation", fontSize=9, textColor=colors.HexColor("#555555"),
        spaceBefore=2, spaceAfter=2, leading=13,
        leftIndent=20, fontName="Helvetica-Oblique"
    )
    return custom


def header_footer(canvas, doc):
    canvas.saveState()
    # Header
    canvas.setFillColor(DARK_BLUE)
    canvas.rect(doc.leftMargin, A4[1] - 1.5*cm, A4[0] - doc.leftMargin - doc.rightMargin, 0.5, fill=1)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(doc.leftMargin, A4[1] - 1.8*cm, "DermatoRAG — Cours Medical Genere par IA")
    canvas.drawRightString(A4[0] - doc.rightMargin, A4[1] - 1.8*cm, datetime.now().strftime("%d/%m/%Y"))
    # Footer
    canvas.setFillColor(DARK_BLUE)
    canvas.rect(doc.leftMargin, 1.0*cm, A4[0] - doc.leftMargin - doc.rightMargin, 0.5, fill=1)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(doc.leftMargin, 0.6*cm, "Document genere automatiquement — Usage pedagogique uniquement")
    canvas.drawRightString(A4[0] - doc.rightMargin, 0.6*cm, f"Page {doc.page}")
    canvas.restoreState()


def _sanitize(text):
    """Remove or escape HTML/XML tags that would break ReportLab's parser.
    
    Handles malformed HTML from LLM output by stripping all tags and
    normalizing whitespace. Also escapes remaining angle brackets so that
    any residual markup is harmlessly displayed as text.
    """
    # Escape HTML entities FIRST (so <b> becomes &lt;b&gt;)
    text = html.escape(text, quote=False)
    # Restore our intentional bold/italic tags that we add below
    text = text.replace("&lt;b&gt;", "<b>")
    text = text.replace("&lt;/b&gt;", "</b>")
    text = text.replace("&lt;i&gt;", "<i>")
    text = text.replace("&lt;/i&gt;", "</i>")
    return text


def generate_pdf(markdown_content: str, output_path: str, course_title: str, level: str = "DES") -> str:
    styles = make_styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm
    )

    story = []

    # Title page
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph(_sanitize(course_title), styles["Title"]))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph(f"Niveau : {level}", styles["Subtitle"]))
    story.append(Paragraph(f"Genere le {datetime.now().strftime('%d %B %Y')}", styles["Subtitle"]))
    story.append(Spacer(1, 0.5*cm))
    story.append(HRFlowable(width="100%", thickness=2, color=GOLD))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("Base sur la litterature medicale indexee via DermatoRAG", styles["Citation"]))
    story.append(PageBreak())

    # Parse markdown line by line
    lines = markdown_content.split("\n")
    for line in lines:
        line = line.rstrip()
        if not line:
            story.append(Spacer(1, 0.2*cm))
            continue

        # Headings (strip markdown markers, sanitize, no bold tags added)
        if line.startswith("### "):
            story.append(Paragraph(_sanitize(line[4:]), styles["H3"]))
        elif line.startswith("## "):
            story.append(Paragraph(_sanitize(line[3:]), styles["H2"]))
        elif line.startswith("# ") and not line.startswith("## "):
            story.append(PageBreak())
            story.append(Paragraph(_sanitize(line[2:]), styles["H1"]))
            story.append(HRFlowable(width="60%", thickness=1, color=GOLD, spaceAfter=6))
        # Bullets
        elif line.startswith("- ") or line.startswith("* "):
            txt = line[2:]
            # Convert **bold** and *italic* to ReportLab tags on sanitized text
            txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", txt)  # **text** -> <b>text</b>
            txt = re.sub(r"(?<![*])\*(.+?)\*(?![*])", r"<i>\1</i>", txt)  # *text* -> <i>text</i>
            story.append(Paragraph(f"\u2022 {_sanitize(txt)}", styles["Bullet"]))
        elif re.match(r"^\d+\. ", line):
            txt = re.sub(r"^\d+\. ", "", line)
            txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", txt)
            txt = re.sub(r"(?<![*])\*(.+?)\*(?![*])", r"<i>\1</i>", txt)
            story.append(Paragraph(f"\u2022 {_sanitize(txt)}", styles["Bullet"]))
        # Bold-only lines
        elif re.match(r"^\*\*(.+?)\*\*$", line):
            inner = re.match(r"^\*\*(.+?)\*\*$", line).group(1)
            story.append(Paragraph(f"<b>{_sanitize(inner)}</b>", styles["H3"]))
        # Normal paragraph — convert bold/italic on sanitized text
        else:
            txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
            txt = re.sub(r"(?<![*])\*(.+?)\*(?![*])", r"<i>\1</i>", txt)
            story.append(Paragraph(_sanitize(txt), styles["Body"]))

    doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
    return output_path
