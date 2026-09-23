import os
import io
import csv
import json
from datetime import datetime
from typing import Dict, Any, List
from pathlib import Path
from backend.config import REPORTS_DIR

# Friendly labels for the twelve extracted declaration fields (shared by every export format).
DECLARATION_LABELS: List[tuple] = [
    ("product_name", "Product / Generic Identity"),
    ("brand", "Brand"),
    ("category", "Commodity Category"),
    ("net_quantity", "Net Quantity"),
    ("mrp", "Maximum Retail Price (MRP)"),
    ("unit_sale_price", "Unit Sale Price (USP)"),
    ("mfg_date", "Month & Year of Manufacture / Packing"),
    ("best_before", "Best Before / Expiry"),
    ("manufacturer", "Manufacturer / Packer / Importer"),
    ("consumer_care", "Consumer Care Details"),
    ("fssai_license", "FSSAI Licence Number"),
    ("country_of_origin", "Country of Origin"),
]

STATUS_LABELS = {
    "COMPLIANT": "COMPLIANT",
    "NON_COMPLIANT": "NON-COMPLIANT",
    "REVIEW_REQUIRED": "REQUIRES MANUAL REVIEW",
}

# Human-readable labels for violation_diagnostics.py's root_cause codes.
ROOT_CAUSE_LABELS = {
    "GENUINELY_ABSENT": "Declaration Genuinely Absent",
    "PRINT_QUALITY_DEGRADED": "Print Quality Degraded",
    "WRONG_PANEL_LIKELY": "Wrong Panel Likely Scanned",
    "NON_STANDARD_FORMAT": "Non-Standard Format",
    "UNDERSIZED_TEXT": "Undersized Text",
}


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status or "UNKNOWN")


def _root_cause_label(root_cause: Any) -> str:
    return ROOT_CAUSE_LABELS.get(root_cause, root_cause or "")


def _conf_pct(value: Any) -> str:
    return f"{int(round(value * 100))}%" if isinstance(value, (int, float)) and value else "—"

def generate_html_report(inspection: Dict[str, Any]) -> str:
    """
    Generates a formal, printable Legal Metrology digital compliance inspection certificate.
    """
    insp_id = inspection["inspection_id"]
    timestamp = inspection["timestamp"]
    status = inspection["overall_status"]
    conf = int(inspection["overall_confidence"] * 100)
    summary = inspection["summary"]
    extracted = inspection.get("extracted_data", {})
    rules = inspection.get("rule_results", [])
    violations = inspection.get("violations", [])
    officer = inspection.get("officer_verification") or {}

    status_color = "#10b981" if status == "COMPLIANT" else ("#ef4444" if status == "NON_COMPLIANT" else "#f59e0b")
    status_label = "COMPLIANT" if status == "COMPLIANT" else ("NON-COMPLIANT" if status == "NON_COMPLIANT" else "REQUIRES MANUAL REVIEW")

    rules_rows_html = ""
    for r in rules:
        r_status = r["status"]
        r_color = "#10b981" if r_status == "PASS" else ("#ef4444" if r_status == "FAIL" else "#f59e0b")
        root_cause = r.get("root_cause")
        rectification = r.get("rectification")
        diagnosis_html = ""
        if root_cause and rectification:
            diagnosis_html = f"""
            <div style="margin-top: 6px; padding: 8px 10px; background: #eff6ff; border-left: 3px solid #3b82f6; border-radius: 4px;">
                <div style="font-size: 10px; font-weight: 700; text-transform: uppercase; color: #1e40af; letter-spacing: 0.5px;">Root Cause: {_root_cause_label(root_cause)}</div>
                <div style="font-size: 12px; color: #1f2937; margin-top: 3px;"><strong>Recommended fix:</strong> {rectification}</div>
            </div>
            """
        rules_rows_html += f"""
        <tr>
            <td style="padding: 10px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">{r["rule_id"]}</td>
            <td style="padding: 10px; border-bottom: 1px solid #e5e7eb;">
                <div><strong>{r["title"]}</strong></div>
                <div style="font-size: 11px; color: #6b7280;">{r["legal_reference"]}</div>
            </td>
            <td style="padding: 10px; border-bottom: 1px solid #e5e7eb;">
                <span style="display: inline-block; padding: 4px 10px; border-radius: 9999px; font-size: 11px; font-weight: 700; background: {r_color}22; color: {r_color};">
                    {r_status}
                </span>
            </td>
            <td style="padding: 10px; border-bottom: 1px solid #e5e7eb; font-size: 12px; color: #374151;">
                {r["message"]}
                {diagnosis_html}
            </td>
        </tr>
        """

    violations_html = ""
    if violations:
        violations_html = """
        <div style="background: #fef2f2; border-left: 4px solid #ef4444; padding: 14px; margin: 18px 0; border-radius: 4px;">
            <h4 style="margin: 0 0 8px 0; color: #991b1b; font-size: 14px; text-transform: uppercase;">Statutory Non-Compliance Notices:</h4>
            <ul style="margin: 0; padding-left: 20px; color: #b91c1c; font-size: 13px;">
        """
        for v in violations:
            violations_html += f"<li style='margin-bottom: 4px;'>{v}</li>"
        violations_html += "</ul></div>"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Compliance Inspection Certificate - {insp_id}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: #1f2937;
            background: #f9fafb;
            margin: 0;
            padding: 24px;
        }}
        .certificate-container {{
            max-width: 860px;
            margin: 0 auto;
            background: #ffffff;
            border: 1px solid #e5e7eb;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            border-radius: 8px;
            padding: 36px;
        }}
        .header {{
            text-align: center;
            border-bottom: 2px solid #1e3a8a;
            padding-bottom: 20px;
            margin-bottom: 24px;
        }}
        .header h1 {{
            margin: 0;
            font-size: 20px;
            color: #1e3a8a;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .header h2 {{
            margin: 6px 0 0 0;
            font-size: 14px;
            font-weight: 500;
            color: #4b5563;
        }}
        .header .badge-id {{
            display: inline-block;
            margin-top: 10px;
            background: #f3f4f6;
            color: #1f2937;
            padding: 4px 12px;
            border-radius: 6px;
            font-family: monospace;
            font-size: 12px;
            font-weight: 600;
        }}
        .status-banner {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: {status_color}15;
            border: 2px solid {status_color};
            border-radius: 8px;
            padding: 16px 24px;
            margin-bottom: 24px;
        }}
        .status-title {{
            font-size: 22px;
            font-weight: 800;
            color: {status_color};
            margin: 0;
        }}
        .meta-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }}
        .meta-card {{
            background: #f9fafb;
            border: 1px solid #f3f4f6;
            padding: 14px;
            border-radius: 6px;
        }}
        .meta-label {{
            font-size: 11px;
            text-transform: uppercase;
            color: #6b7280;
            font-weight: 600;
            margin-bottom: 4px;
        }}
        .meta-value {{
            font-size: 14px;
            font-weight: 600;
            color: #111827;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
        }}
        th {{
            background: #f3f4f6;
            text-align: left;
            padding: 10px;
            font-size: 12px;
            color: #4b5563;
            text-transform: uppercase;
        }}
        .signature-block {{
            display: flex;
            justify-content: space-between;
            margin-top: 48px;
            padding-top: 24px;
            border-top: 1px dashed #d1d5db;
        }}
        .sig-box {{
            text-align: center;
            width: 220px;
        }}
        .sig-line {{
            border-top: 1px solid #4b5563;
            margin-top: 40px;
            padding-top: 6px;
            font-size: 12px;
            font-weight: 600;
        }}
        @media print {{
            body {{ background: #fff; padding: 0; }}
            .certificate-container {{ border: none; box-shadow: none; padding: 0; }}
            .no-print {{ display: none; }}
        }}
    </style>
</head>
<body>
    <div class="no-print" style="text-align: right; max-width: 860px; margin: 0 auto 16px auto;">
        <button onclick="window.print()" style="background: #1e3a8a; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">
            Print / Save as PDF
        </button>
    </div>

    <div class="certificate-container">
        <div class="header">
            <h1>Department of Consumer Affairs</h1>
            <h2>Legal Metrology Division &bull; Compliance Inspection Certificate</h2>
            <div style="font-size: 12px; color: #6b7280; margin-top: 4px;">Issued under Legal Metrology Act, 2009 & Packaged Commodities Rules, 2011</div>
            <div class="badge-id">Inspection Reference: {insp_id} &bull; Timestamp: {timestamp} {" &bull; <strong>[Dual-Panel Synchronized]</strong>" if inspection.get("is_dual_panel") else ""}</div>
        </div>

        <div class="status-banner">
            <div>
                <h3 class="status-title">{status_label}</h3>
                <div style="font-size: 13px; color: #4b5563; margin-top: 4px;">{summary}</div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 12px; color: #6b7280; font-weight: 600;">AI Pipeline Confidence</div>
                <div style="font-size: 26px; font-weight: 800; color: {status_color};">{conf}%</div>
            </div>
        </div>

        {violations_html}

        <h3 style="font-size: 15px; border-bottom: 2px solid #e5e7eb; padding-bottom: 6px; margin-bottom: 12px;">1. Commodity Identification & Metadata</h3>
        <div class="meta-grid">
            <div class="meta-card">
                <div class="meta-label">Product / Generic Identity</div>
                <div class="meta-value">{extracted.get("product_name", {}).get("value") or "Not Identified"}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Brand / Commodity Category</div>
                <div class="meta-value">{extracted.get("brand", {}).get("value") or "Unknown Brand"} &bull; {extracted.get("category", {}).get("value") or "Packaged Commodity"}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Declared Net Quantity</div>
                <div class="meta-value">{extracted.get("net_quantity", {}).get("value") or "Missing / Undetected"}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Maximum Retail Price (MRP)</div>
                <div class="meta-value">{extracted.get("mrp", {}).get("value") or "Missing / Undetected"}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Unit Sale Price (USP) [Rule 6(10)]</div>
                <div class="meta-value">{extracted.get("unit_sale_price", {}).get("value") or "Not Declared / N/A"}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Manufacturer / Packer Details</div>
                <div class="meta-value">{extracted.get("manufacturer", {}).get("value") or "Missing / Undetected"}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Consumer Care Cell Contact</div>
                <div class="meta-value">{extracted.get("consumer_care", {}).get("value") or "Missing / Undetected"}</div>
            </div>
        </div>

        <h3 style="font-size: 15px; border-bottom: 2px solid #e5e7eb; padding-bottom: 6px; margin: 24px 0 12px 0;">2. Statutory Rule Evaluation Audit</h3>
        <table>
            <thead>
                <tr>
                    <th style="width: 15%;">Rule ID</th>
                    <th style="width: 35%;">Provision</th>
                    <th style="width: 15%;">Status</th>
                    <th style="width: 35%;">Evaluation Details</th>
                </tr>
            </thead>
            <tbody>
                {rules_rows_html}
            </tbody>
        </table>

        <div class="signature-block">
            <div class="sig-box">
                <div style="font-size: 11px; color: #6b7280;">Automated AI Engine</div>
                <div style="font-family: monospace; font-size: 11px; color: #1e3a8a; margin-top: 14px;">LM-CLIP-OCR-v2.1</div>
                <div class="sig-line">System Verification Stamp</div>
            </div>
            <div class="sig-box">
                <div style="font-size: 11px; color: #6b7280;">Enforcement Official</div>
                <div style="font-size: 12px; font-weight: 600; margin-top: 14px;">{officer.get("officer_name", "Inspecting Officer")}</div>
                <div class="sig-line">{officer.get("officer_id", "Legal Metrology Inspector")}</div>
            </div>
        </div>
    </div>
</body>
</html>
"""
    # Save to reports directory
    report_file = Path(REPORTS_DIR) / f"report_{insp_id}.html"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_content


# ---------------------------------------------------------------------------
# Machine-readable / editable exports (JSON, CSV) + printable PDF
# ---------------------------------------------------------------------------

def generate_json_export(inspection: Dict[str, Any]) -> Dict[str, Any]:
    """Structured, re-importable view of an inspection for downstream editing / MIS."""
    extracted = inspection.get("extracted_data", {}) or {}

    declarations = {}
    for key, label in DECLARATION_LABELS:
        fld = extracted.get(key) or {}
        declarations[key] = {
            "label": label,
            "value": fld.get("value"),
            "confidence": round(float(fld.get("confidence", 0.0) or 0.0), 3),
            "is_valid": bool(fld.get("is_valid", False)),
        }

    return {
        "export_format_version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inspection_id": inspection.get("inspection_id"),
        "timestamp": inspection.get("timestamp"),
        "image_filename": inspection.get("image_filename"),
        "is_dual_panel": bool(inspection.get("is_dual_panel", False)),
        "overall_status": inspection.get("overall_status"),
        "overall_status_label": _status_label(inspection.get("overall_status", "")),
        "overall_confidence": inspection.get("overall_confidence"),
        "summary": inspection.get("summary"),
        "declarations": declarations,
        "rule_results": [
            {
                "rule_id": r.get("rule_id"),
                "title": r.get("title"),
                "legal_reference": r.get("legal_reference"),
                "status": r.get("status"),
                "severity": r.get("severity"),
                "message": r.get("message"),
                "confidence": r.get("confidence"),
                "root_cause": r.get("root_cause"),
                "root_cause_label": _root_cause_label(r.get("root_cause")) if r.get("root_cause") else None,
                "rectification": r.get("rectification") or None,
            }
            for r in inspection.get("rule_results", [])
        ],
        "violations": inspection.get("violations", []),
        "officer_verification": inspection.get("officer_verification"),
    }


def generate_csv_export(inspection: Dict[str, Any]) -> str:
    """Flat single-table CSV: metadata rows, then one row per declaration / rule / violation."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["inspection_id", "timestamp", "image_filename",
                     "overall_status", "overall_confidence", "dual_panel"])
    writer.writerow([
        inspection.get("inspection_id", ""),
        inspection.get("timestamp", ""),
        inspection.get("image_filename", ""),
        _status_label(inspection.get("overall_status", "")),
        inspection.get("overall_confidence", ""),
        "yes" if inspection.get("is_dual_panel") else "no",
    ])
    writer.writerow([])

    writer.writerow(["section", "key", "title", "value", "confidence",
                     "status", "severity", "legal_reference", "message",
                     "root_cause", "rectification"])

    extracted = inspection.get("extracted_data", {}) or {}
    for key, label in DECLARATION_LABELS:
        fld = extracted.get(key) or {}
        writer.writerow([
            "declaration", key, label, fld.get("value") or "",
            fld.get("confidence", ""),
            "valid" if fld.get("is_valid") else "review",
            "", "", "", "", "",
        ])

    for r in inspection.get("rule_results", []):
        writer.writerow([
            "rule", r.get("rule_id", ""), r.get("title", ""), "",
            r.get("confidence", ""), r.get("status", ""), r.get("severity", ""),
            r.get("legal_reference", ""), r.get("message", ""),
            _root_cause_label(r.get("root_cause")) if r.get("root_cause") else "",
            r.get("rectification", ""),
        ])

    for v in inspection.get("violations", []):
        writer.writerow(["violation", "", "", "", "", "", "", "", v, "", ""])

    return buf.getvalue()


def generate_pdf_report(inspection: Dict[str, Any]) -> bytes:
    """Render a printable compliance certificate as a PDF (requires 'reportlab')."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        )
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "PDF export requires the 'reportlab' package. Install it with "
            "'pip install reportlab' (already listed in requirements.txt)."
        ) from exc

    insp_id = inspection.get("inspection_id", "UNKNOWN")
    status = inspection.get("overall_status", "")
    status_color = {
        "COMPLIANT": colors.HexColor("#10b981"),
        "NON_COMPLIANT": colors.HexColor("#ef4444"),
    }.get(status, colors.HexColor("#f59e0b"))
    conf_pct = int(round(float(inspection.get("overall_confidence", 0.0) or 0.0) * 100))

    styles = getSampleStyleSheet()
    h_title = ParagraphStyle("h_title", parent=styles["Title"], fontSize=15,
                             textColor=colors.HexColor("#1e3a8a"), alignment=TA_CENTER, spaceAfter=2)
    h_sub = ParagraphStyle("h_sub", parent=styles["Normal"], fontSize=9,
                           textColor=colors.HexColor("#4b5563"), alignment=TA_CENTER)
    h_sec = ParagraphStyle("h_sec", parent=styles["Heading3"], fontSize=11,
                           textColor=colors.HexColor("#111827"), spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=8.5, leading=11)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8, leading=10)

    story: List[Any] = [
        Paragraph("Department of Consumer Affairs", h_title),
        Paragraph("Legal Metrology Division &bull; Compliance Inspection Certificate", h_sub),
        Paragraph("Issued under the Legal Metrology Act, 2009 &amp; Packaged Commodities Rules, 2011", h_sub),
        Spacer(1, 10),
    ]

    meta = Table(
        [
            ["Inspection Reference", insp_id, "Date / Time", inspection.get("timestamp", "—")],
            ["Image / Sample", inspection.get("image_filename", "—"),
             "Inspection Mode", "Dual-Panel (Front + Back)" if inspection.get("is_dual_panel") else "Single Panel"],
        ],
        colWidths=[38 * mm, 55 * mm, 32 * mm, 45 * mm],
    )
    meta.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f3f4f6")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6b7280")),
        ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#6b7280")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(meta)
    story.append(Spacer(1, 8))

    banner = Table(
        [[Paragraph(f"<b>{_status_label(status)}</b>", ParagraphStyle(
            "st", parent=body, fontSize=13, textColor=colors.white)),
          Paragraph(f"<b>AI Pipeline Confidence: {conf_pct}%</b>", ParagraphStyle(
              "sc", parent=body, fontSize=10, textColor=colors.white, alignment=2))]],
        colWidths=[110 * mm, 60 * mm],
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), status_color),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(banner)
    story.append(Paragraph(inspection.get("summary", ""), body))

    violations = inspection.get("violations", [])
    if violations:
        story.append(Paragraph("Statutory Non-Compliance Notices", h_sec))
        story.append(Table(
            [[Paragraph(f"&bull; {v}", ParagraphStyle("v", parent=cell,
                                                      textColor=colors.HexColor("#b91c1c")))]
             for v in violations],
            colWidths=[170 * mm],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef2f2")),
                ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#ef4444")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]),
        ))

    # Extracted declarations
    story.append(Paragraph("1. Commodity Declarations", h_sec))
    extracted = inspection.get("extracted_data", {}) or {}
    decl_data = [["Declaration", "Detected Value", "Conf."]]
    for key, label in DECLARATION_LABELS:
        fld = extracted.get(key) or {}
        decl_data.append([
            Paragraph(label, cell),
            Paragraph(str(fld.get("value") or "— not detected —"), cell),
            _conf_pct(fld.get("confidence")),
        ])
    decl_table = Table(decl_data, colWidths=[62 * mm, 92 * mm, 16 * mm], repeatRows=1)
    decl_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(decl_table)

    # Rule evaluation
    story.append(Paragraph("2. Statutory Rule Evaluation", h_sec))
    rule_data = [["Rule", "Provision", "Status", "Findings"]]
    for r in inspection.get("rule_results", []):
        findings_html = str(r.get("message", ""))
        if r.get("root_cause") and r.get("rectification"):
            findings_html += (
                f"<br/><font size=6.5 color='#1e40af'><b>Root Cause: {_root_cause_label(r.get('root_cause'))}</b></font>"
                f"<br/><font size=7 color='#1f2937'><b>Fix:</b> {r.get('rectification')}</font>"
            )
        rule_data.append([
            Paragraph(str(r.get("rule_id", "")), cell),
            Paragraph(f"{r.get('title', '')}<br/><font size=6 color='#6b7280'>{r.get('legal_reference', '')}</font>", cell),
            Paragraph(str(r.get("status", "")), cell),
            Paragraph(findings_html, cell),
        ])
    rule_table = Table(rule_data, colWidths=[24 * mm, 52 * mm, 16 * mm, 78 * mm], repeatRows=1)
    rule_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(rule_table)

    officer = inspection.get("officer_verification") or {}
    story.append(Spacer(1, 16))
    sign = Table(
        [["System Verification Stamp", "Enforcement Official"],
         ["LM-CLIP-OCR-v2.1", officer.get("officer_name") or "Inspecting Officer"],
         ["Automated AI Engine", officer.get("officer_id") or "Legal Metrology Inspector"]],
        colWidths=[85 * mm, 85 * mm],
    )
    sign.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LINEABOVE", (0, 1), (-1, 1), 0.5, colors.HexColor("#4b5563")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6b7280")),
        ("TEXTCOLOR", (0, 2), (-1, 2), colors.HexColor("#6b7280")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(sign)

    buf = io.BytesIO()
    SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Compliance Certificate {insp_id}",
    ).build(story)
    pdf_bytes = buf.getvalue()
    buf.close()

    try:
        with open(Path(REPORTS_DIR) / f"report_{insp_id}.pdf", "wb") as f:
            f.write(pdf_bytes)
    except OSError:
        pass

    return pdf_bytes


def generate_docx_report(inspection: Dict[str, Any]) -> bytes:
    """Render the compliance certificate as an editable Word (.docx) document.

    Unlike the PDF/HTML certificates this is meant to be opened and edited
    (e.g. an officer annotating findings before filing), so content is plain
    paragraphs/tables rather than a fixed visual layout. Requires 'python-docx'.
    """
    try:
        from docx import Document
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt, RGBColor
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "DOCX export requires the 'python-docx' package. Install it with "
            "'pip install python-docx' (already listed in requirements.txt)."
        ) from exc

    insp_id = inspection.get("inspection_id", "UNKNOWN")
    status = inspection.get("overall_status", "")
    status_rgb = {
        "COMPLIANT": RGBColor(0x10, 0xB9, 0x81),
        "NON_COMPLIANT": RGBColor(0xEF, 0x44, 0x44),
    }.get(status, RGBColor(0xF5, 0x9E, 0x0B))
    conf_pct = int(round(float(inspection.get("overall_confidence", 0.0) or 0.0) * 100))

    doc = Document()

    title = doc.add_heading("Department of Consumer Affairs", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph("Legal Metrology Division • Compliance Inspection Certificate")
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ref = doc.add_paragraph(
        f"Issued under the Legal Metrology Act, 2009 & Packaged Commodities Rules, 2011\n"
        f"Inspection Reference: {insp_id}  •  Timestamp: {inspection.get('timestamp', '—')}"
    )
    ref.alignment = WD_ALIGN_PARAGRAPH.CENTER

    status_p = doc.add_paragraph()
    status_run = status_p.add_run(f"{_status_label(status)}  —  AI Pipeline Confidence: {conf_pct}%")
    status_run.bold = True
    status_run.font.size = Pt(14)
    status_run.font.color.rgb = status_rgb
    doc.add_paragraph(inspection.get("summary", ""))

    violations = inspection.get("violations", [])
    if violations:
        doc.add_heading("Statutory Non-Compliance Notices", level=2)
        for v in violations:
            doc.add_paragraph(v, style="List Bullet")

    doc.add_heading("1. Commodity Declarations", level=2)
    extracted = inspection.get("extracted_data", {}) or {}
    decl_table = doc.add_table(rows=1, cols=3)
    decl_table.style = "Light Grid Accent 1"
    decl_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = decl_table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Declaration", "Detected Value", "Confidence"
    for key, label in DECLARATION_LABELS:
        fld = extracted.get(key) or {}
        row = decl_table.add_row().cells
        row[0].text = label
        row[1].text = str(fld.get("value") or "— not detected —")
        row[2].text = _conf_pct(fld.get("confidence"))

    doc.add_heading("2. Statutory Rule Evaluation", level=2)
    rule_table = doc.add_table(rows=1, cols=4)
    rule_table.style = "Light Grid Accent 1"
    rule_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    rhdr = rule_table.rows[0].cells
    rhdr[0].text, rhdr[1].text, rhdr[2].text, rhdr[3].text = "Rule", "Provision", "Status", "Findings"
    for r in inspection.get("rule_results", []):
        row = rule_table.add_row().cells
        row[0].text = str(r.get("rule_id", ""))
        row[1].text = f"{r.get('title', '')} ({r.get('legal_reference', '')})"
        row[2].text = str(r.get("status", ""))
        findings_text = str(r.get("message", ""))
        if r.get("root_cause") and r.get("rectification"):
            findings_text += f"\nRoot Cause: {_root_cause_label(r.get('root_cause'))}\nFix: {r.get('rectification')}"
        row[3].text = findings_text

    officer = inspection.get("officer_verification") or {}
    doc.add_heading("Verification", level=2)
    sign_table = doc.add_table(rows=2, cols=2)
    sign_table.style = "Light Grid Accent 1"
    sign_table.rows[0].cells[0].text = "System Verification Stamp"
    sign_table.rows[0].cells[1].text = "Enforcement Official"
    sign_table.rows[1].cells[0].text = "LM-CLIP-OCR-v2.1 (Automated AI Engine)"
    sign_table.rows[1].cells[1].text = officer.get("officer_name") or officer.get("officer_id") or "Inspecting Officer (unassigned)"

    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()
    buf.close()

    try:
        with open(Path(REPORTS_DIR) / f"report_{insp_id}.docx", "wb") as f:
            f.write(docx_bytes)
    except OSError:
        pass

    return docx_bytes
