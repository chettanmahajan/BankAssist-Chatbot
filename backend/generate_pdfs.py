"""Generate PDF copies of every .txt under data/. Run: python -m backend.generate_pdfs"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

logger = logging.getLogger("generate_pdfs")


def _txt_to_pdf(txt_path: Path, pdf_path: Path) -> None:
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
    )
    title_style = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        spaceAfter=10,
    )

    text = txt_path.read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=txt_path.stem.replace("_", " ").title(),
    )

    story = [Paragraph(txt_path.stem.replace("_", " ").title(), title_style), Spacer(1, 6)]
    for para in paragraphs:
        safe = (
            para.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )
        story.append(Paragraph(safe, body))
        story.append(Spacer(1, 4))

    doc.build(story)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate PDF copies of every .txt under data/.")
    parser.add_argument("--data-dir", default="data", help="Root directory containing .txt files")
    parser.add_argument("--clean", action="store_true", help="Delete existing PDFs before regenerating")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root = Path(args.data_dir).resolve()
    if not root.exists():
        logger.error("data dir not found: %s", root)
        return 1

    if args.clean:
        for p in root.rglob("*.pdf"):
            p.unlink()
            logger.info("removed %s", p.relative_to(root.parent))

    txt_files = sorted(root.rglob("*.txt"))
    if not txt_files:
        logger.warning("no .txt files under %s", root)
        return 0

    for txt in txt_files:
        pdf = txt.with_suffix(".pdf")
        _txt_to_pdf(txt, pdf)
        logger.info("wrote %s", pdf.relative_to(root.parent))

    print(f"\nGenerated {len(txt_files)} PDFs under {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
