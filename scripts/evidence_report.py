#!/usr/bin/env python3
"""
evidence_report.py — Generate an auditor-facing explainer PDF for Vanta evidence.

Produces a clean, professional PDF with:
  - Cover header with title and date
  - "What was requested" section (auditor ask)
  - "What is provided" section with embedded screenshot thumbnails
  - "Explanation" section (agent-written context)

Usage:
  python3 evidence_report.py \
    --title "Evidence: Change Management Controls" \
    --description "Provide evidence of approved change requests..." \
    --files "01_github_pr.png,02_ci_checks.png" \
    --explanation "Screenshots show a merged PR with required approvals..." \
    --output ~/Downloads/vanta-evidence/2026-05-24-CC8.1/evidence_CC8.1.pdf

  # Or pipe JSON to stdin:
  echo '{"title":"...","description":"...","files":["a.png"],...}' | python3 evidence_report.py --stdin
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import provenance

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm, inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
    PageBreak, HRFlowable,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER


WIDTH, HEIGHT = A4
MARGIN = 20 * mm


def esc(text) -> str:
    """Escape dynamic text for reportlab Paragraphs.

    reportlab parses Paragraph content as mini-HTML, so raw '&', '<', '>' in
    dynamic values (notably source URLs with query strings like '&live=false')
    get mangled into entities. Escape any value that originates from data, never
    the hardcoded markup strings in this file.
    """
    return _xml_escape(str(text)) if text is not None else ""


def build_styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "EvidenceTitle",
            parent=base["Heading1"],
            fontSize=20,
            spaceAfter=6,
            textColor=colors.HexColor("#1a1a2e"),
        ),
        "subtitle": ParagraphStyle(
            "EvidenceSubtitle",
            parent=base["Normal"],
            fontSize=11,
            textColor=colors.HexColor("#666666"),
            spaceAfter=20,
        ),
        "heading": ParagraphStyle(
            "SectionHeading",
            parent=base["Heading2"],
            fontSize=14,
            spaceBefore=16,
            spaceAfter=8,
            textColor=colors.HexColor("#16213e"),
        ),
        "body": ParagraphStyle(
            "EvidenceBody",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            spaceAfter=8,
        ),
        "caption": ParagraphStyle(
            "ImageCaption",
            parent=base["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#555555"),
            alignment=TA_CENTER,
            spaceBefore=4,
            spaceAfter=12,
        ),
        "footer": ParagraphStyle(
            "Footer",
            parent=base["Normal"],
            fontSize=8,
            textColor=colors.HexColor("#999999"),
            alignment=TA_CENTER,
        ),
        "provcell": ParagraphStyle(
            "ProvCell",
            parent=base["Normal"],
            fontSize=8,
            leading=10,
            wordWrap="CJK",
        ),
        "provmono": ParagraphStyle(
            "ProvMono",
            parent=base["Normal"],
            fontName="Courier",
            fontSize=7,
            leading=8.5,
            wordWrap="CJK",
            textColor=colors.HexColor("#333333"),
        ),
    }
    return styles


def add_header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#999999"))
    canvas.drawString(MARGIN, HEIGHT - 12 * mm, "EVIDENCE PACKAGE")
    canvas.drawRightString(WIDTH - MARGIN, HEIGHT - 12 * mm,
                           f"Generated {date.today():%B %d, %Y}")
    canvas.drawCentredString(WIDTH / 2, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf(title: str, description: str, files: list[str],
              explanation: str, output: str,
              operator: str | None = None, add_provenance: bool = True):
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir = out_path.parent

    operator = operator or provenance.get_operator()
    generated_at = provenance.utc_now_iso()
    prov_index = provenance.index_by_file(out_dir)
    prov_rows = []

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=25 * mm,
        bottomMargin=20 * mm,
    )

    styles = build_styles()
    story = []

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(esc(title), styles["title"]))
    story.append(Paragraph(
        f"Date: {date.today():%Y-%m-%d}  ·  Prepared by: {esc(operator)}  ·  "
        f"Generated: {esc(generated_at)}",
        styles["subtitle"],
    ))
    story.append(HRFlowable(width="100%", thickness=1,
                            color=colors.HexColor("#e0e0e0"),
                            spaceAfter=10))

    story.append(Paragraph("What was requested", styles["heading"]))
    for para in description.split("\n"):
        para = para.strip()
        if para:
            story.append(Paragraph(esc(para), styles["body"]))

    story.append(Spacer(1, 6 * mm))

    if files:
        story.append(Paragraph("Evidence provided", styles["heading"]))

        available_width = WIDTH - 2 * MARGIN
        max_img_width = available_width * 0.95

        for f in files:
            fp = Path(f)
            if not fp.exists():
                alt = out_dir / fp.name
                if alt.exists():
                    fp = alt
                else:
                    story.append(Paragraph(esc(f"[File not found: {fp.name}]"), styles["body"]))
                    continue

            meta = prov_index.get(fp.name, {})
            prov_rows.append({
                "file": fp.name,
                "source_url": meta.get("source_url", ""),
                "captured_at": meta.get("captured_at", ""),
                "sha256": provenance.sha256_file(fp),
            })

            if fp.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                try:
                    img = Image(str(fp))
                    iw, ih = img.imageWidth, img.imageHeight
                    if iw <= 0:
                        story.append(Paragraph(esc(f"[Invalid image: {fp.name}]"), styles["body"]))
                        continue

                    scale = min(max_img_width / iw, 1.0)
                    max_img_height = 500
                    if ih * scale > max_img_height:
                        scale = max_img_height / ih

                    img.drawWidth = iw * scale
                    img.drawHeight = ih * scale
                    img.hAlign = "CENTER"

                    story.append(img)
                    story.append(Paragraph(esc(fp.name), styles["caption"]))
                except Exception as e:
                    story.append(Paragraph(esc(f"[Error loading {fp.name}: {e}]"), styles["body"]))
            elif fp.suffix.lower() == ".pdf":
                story.append(Paragraph(
                    f"Attached PDF: <b>{esc(fp.name)}</b> ({fp.stat().st_size // 1024} KB)",
                    styles["body"],
                ))
            else:
                story.append(Paragraph(f"Attached: <b>{esc(fp.name)}</b>", styles["body"]))

    if explanation:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("Explanation", styles["heading"]))
        for para in explanation.split("\n"):
            para = para.strip()
            if para:
                story.append(Paragraph(esc(para), styles["body"]))

    if add_provenance and prov_rows:
        story.append(Spacer(1, 8 * mm))
        story.append(Paragraph("Provenance", styles["heading"]))
        story.append(Paragraph(
            "Each item below is recorded with the source it was captured from, "
            "the capture time (UTC), and a SHA-256 checksum of the exact bytes. "
            "Re-hashing any file and comparing it to its checksum confirms it is "
            "unchanged since capture. The full machine-readable record, including "
            "this document's own checksum, is in <font face='Courier'>manifest.json</font> "
            "alongside this package.",
            styles["body"],
        ))
        story.append(Spacer(1, 3 * mm))

        content_w = WIDTH - 2 * MARGIN
        header = [
            Paragraph("<b>File</b>", styles["provcell"]),
            Paragraph("<b>Source</b>", styles["provcell"]),
            Paragraph("<b>Captured (UTC)</b>", styles["provcell"]),
            Paragraph("<b>SHA-256</b>", styles["provcell"]),
        ]
        data = [header]
        for row in prov_rows:
            data.append([
                Paragraph(esc(row["file"]), styles["provcell"]),
                Paragraph(esc(row["source_url"]) or "&mdash;", styles["provcell"]),
                Paragraph(esc(row["captured_at"]) or "&mdash;", styles["provcell"]),
                Paragraph(esc(row["sha256"]), styles["provmono"]),
            ])

        table = Table(data, colWidths=[
            content_w * 0.24, content_w * 0.31,
            content_w * 0.19, content_w * 0.26,
        ])
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d0d0")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f4")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)

    story.append(Spacer(1, 10 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5,
                            color=colors.HexColor("#e0e0e0"),
                            spaceAfter=6))
    story.append(Paragraph(
        "Generated automatically. Each item's source, capture time, operator, "
        "and SHA-256 are recorded in manifest.json alongside this package. "
        "Review contents before submitting to your auditor.",
        styles["footer"],
    ))

    doc.build(story, onFirstPage=add_header_footer, onLaterPages=add_header_footer)

    pdf_sha = provenance.sha256_file(out_path)
    test_id = ""
    m = re.match(r"^\d{4}-\d{2}-\d{2}-(.+)$", out_dir.name)
    if m:
        test_id = m.group(1)
    if add_provenance:
        provenance.record_item(
            out_dir, out_path, kind="explainer_pdf",
            test_id=test_id, sha256=pdf_sha,
        )

    result = {
        "success": True,
        "pdf": str(out_path),
        "pages": doc.page,
        "sha256": pdf_sha,
        "operator": operator,
        "generated_at": generated_at,
        "manifest": str(provenance.manifest_path(out_dir)),
    }
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description="Generate Vanta evidence explainer PDF")
    parser.add_argument("--title", help="Document title")
    parser.add_argument("--description", help="What the auditor asked for")
    parser.add_argument("--files", help="Comma-separated paths to evidence files")
    parser.add_argument("--explanation", help="Agent-written explanation for the auditor")
    parser.add_argument("--output", help="Output PDF path")
    parser.add_argument("--operator",
                        help="Operator identity (default: git user.email or OS user)")
    parser.add_argument("--no-provenance", dest="provenance", action="store_false",
                        help="Skip the provenance table / manifest record")
    parser.set_defaults(provenance=True)
    parser.add_argument("--stdin", action="store_true",
                        help="Read all params as JSON from stdin")

    args = parser.parse_args()

    if args.stdin:
        data = json.load(sys.stdin)
        title = data["title"]
        description = data.get("description", "")
        files = data.get("files", [])
        explanation = data.get("explanation", "")
        output = data["output"]
        operator = data.get("operator")
        add_provenance = data.get("add_provenance", True)
    else:
        if not all([args.title, args.output]):
            parser.error("--title and --output are required (or use --stdin)")
        title = args.title
        description = args.description or ""
        files = [f.strip() for f in (args.files or "").split(",") if f.strip()]
        explanation = args.explanation or ""
        output = args.output
        operator = args.operator
        add_provenance = args.provenance

    build_pdf(title, description, files, explanation, output,
              operator=operator, add_provenance=add_provenance)


if __name__ == "__main__":
    main()
