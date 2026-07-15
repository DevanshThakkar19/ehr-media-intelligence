#!/usr/bin/env python3
"""Render write-up PDF without external deps (ReportLab if available, else text note)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "writeup" / "WRITEUP.md"
OUT = ROOT / "writeup" / "EHR_Media_Intelligence_Writeup.pdf"


def main() -> None:
    text = MD.read_text(encoding="utf-8")
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        # Minimal PDF via pure Python — enough for submission if reportlab missing
        try:
            from fpdf import FPDF
        except ImportError:
            OUT.with_suffix(".txt").write_text(text, encoding="utf-8")
            print(f"Wrote {OUT.with_suffix('.txt')} (install reportlab for PDF)")
            return

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        pdf.set_font("Helvetica", size=10)
        for line in text.splitlines():
            pdf.multi_cell(0, 5, line.encode("latin-1", "replace").decode("latin-1"))
        pdf.output(str(OUT))
        print(f"Wrote {OUT}")
        return

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleCustom",
        parent=styles["Heading1"],
        fontSize=14,
        spaceAfter=8,
    )
    body = ParagraphStyle(
        "BodyCustom",
        parent=styles["BodyText"],
        fontSize=9,
        leading=12,
        spaceAfter=6,
    )
    heading = ParagraphStyle(
        "HeadCustom",
        parent=styles["Heading2"],
        fontSize=11,
        spaceBefore=8,
        spaceAfter=4,
    )

    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
    )
    story = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            story.append(Spacer(1, 4))
            continue
        safe = (
            raw.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        if raw.startswith("# "):
            story.append(Paragraph(safe[2:], title))
        elif raw.startswith("## "):
            story.append(Paragraph(safe[3:], heading))
        elif raw.startswith("- "):
            story.append(Paragraph(f"• {safe[2:]}", body))
        else:
            story.append(Paragraph(safe, body))
    doc.build(story)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
