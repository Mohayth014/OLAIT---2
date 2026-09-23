"""
Manufacturer-specific statutory violation reports.

Generated when a Legal Metrology Officer confirms SEIZURE of an inspection
session: the session's inspected products are grouped by manufacturer/brand and
one report is produced per manufacturer, addressed to that manufacturer's
registered contact.

This is deliberately separate from backend/reporting/report_generator.py, which
produces the per-sample compliance certificate for a single inspection. Nothing
here modifies that module or the compliance engine -- the verdicts, violations
and confidence values are read exactly as the rules engine already computed them.
"""
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import html

from backend.config import REPORTS_DIR


def _product_brand(product: Dict[str, Any]) -> str:
    """Brand for a product: the officer-entered manufacturer if present, else the
    brand the OCR pipeline extracted from its samples."""
    if (product.get("manufacturer") or "").strip():
        return product["manufacturer"].strip()
    for scan in product.get("scans", []):
        brand = ((scan.get("extracted_data") or {}).get("brand") or {}).get("value")
        if brand:
            return str(brand).strip()
    return "Unidentified Manufacturer"


def group_products_by_manufacturer(summary: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Group a session's products under their manufacturer. Only products with at
    least one non-compliant sample are reportable -- a seizure report must be
    backed by an actual detected violation."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for product in summary.get("products", []):
        if (product.get("non_compliant_count") or 0) <= 0:
            continue
        grouped.setdefault(_product_brand(product), []).append(product)
    return grouped


def build_violation_report(
    summary: Dict[str, Any],
    manufacturer: str,
    products: List[Dict[str, Any]],
    officer: Dict[str, Any],
    contact: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble the structured violation report for one manufacturer."""
    violations: List[Dict[str, Any]] = []
    evidence: List[Dict[str, Any]] = []
    product_blocks: List[Dict[str, Any]] = []

    for product in products:
        seen: Dict[str, int] = {}
        for scan in product.get("scans", []):
            if scan.get("overall_status") != "NON_COMPLIANT":
                continue
            scan_violations = scan.get("violations") or []
            for text in scan_violations:
                seen[text] = seen.get(text, 0) + 1
            # A verdict a Controller overrode to NON_COMPLIANT carries no
            # rule-engine violation text -- the written override reason is the
            # statutory basis in that case, so record it rather than reporting
            # a non-compliant product with no stated grounds.
            if not scan_violations:
                for ov in scan.get("override_history") or []:
                    reason = (ov.get("reason") or "").strip()
                    if reason:
                        text = (
                            f"Verdict recorded as non-compliant on review by "
                            f"{ov.get('manager_name') or ov.get('manager_id')}: {reason}"
                        )
                        seen[text] = seen.get(text, 0) + 1
            evidence.append({
                "inspection_id": scan.get("inspection_id"),
                "timestamp": scan.get("timestamp"),
                "image_url": scan.get("image_url") or scan.get("thumbnail_url"),
                "thumbnail_url": scan.get("thumbnail_url"),
                "overall_status": scan.get("overall_status"),
            })
        for text, count in seen.items():
            violations.append({"product_name": product.get("product_name"), "violation": text, "sample_count": count})

        product_blocks.append({
            "product_name": product.get("product_name"),
            "category": product.get("category"),
            "total_stock": product.get("total_quantity"),
            "sample_target": product.get("sample_target"),
            "samples_inspected": product.get("samples_inspected"),
            "compliant_samples": product.get("pass_count"),
            "review_samples": product.get("review_count"),
            "non_compliant_samples": product.get("non_compliant_count"),
            "product_status": product.get("product_status"),
        })

    return {
        "report_type": "STATUTORY_VIOLATION_REPORT",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "manufacturer": manufacturer,
        "brand": (contact or {}).get("brand") or manufacturer,
        "recipient_email": (contact or {}).get("contact_email"),
        "recipient_name": (contact or {}).get("contact_name"),
        "session_id": summary.get("id"),
        "inspection_location": summary.get("location"),
        "inspection_type": summary.get("inspection_type"),
        "inspection_date": summary.get("created_at"),
        "seizure_status": "SEIZED",
        "seized_at": summary.get("seized_at"),
        "officer_name": officer.get("name"),
        "officer_id": officer.get("officer_id"),
        "jurisdiction": summary.get("jurisdiction"),
        "products": product_blocks,
        "products_inspected": len(product_blocks),
        "samples_inspected": sum(p["samples_inspected"] or 0 for p in product_blocks),
        "compliant_samples": sum(p["compliant_samples"] or 0 for p in product_blocks),
        "non_compliant_samples": sum(p["non_compliant_samples"] or 0 for p in product_blocks),
        "violations": violations,
        "evidence": evidence,
        "legal_basis": "Legal Metrology Act, 2009 and the Legal Metrology (Packaged Commodities) Rules, 2011",
    }


def render_violation_report_html(report: Dict[str, Any]) -> str:
    """Printable HTML notice for the manufacturer. Written to REPORTS_DIR and
    attached to the dispatch record."""
    e = lambda v: html.escape(str(v if v is not None else "—"))

    product_rows = "".join(
        f"<tr><td>{e(p['product_name'])}</td><td>{e(p['category'])}</td>"
        f"<td style='text-align:right'>{e(p['total_stock'])}</td>"
        f"<td style='text-align:right'>{e(p['samples_inspected'])}</td>"
        f"<td style='text-align:right'>{e(p['compliant_samples'])}</td>"
        f"<td style='text-align:right;color:#b91c1c;font-weight:700'>{e(p['non_compliant_samples'])}</td></tr>"
        for p in report.get("products", [])
    ) or "<tr><td colspan='6'>—</td></tr>"

    violation_rows = "".join(
        f"<li><strong>{e(v['product_name'])}</strong> — {e(v['violation'])} "
        f"<em>(detected on {e(v['sample_count'])} sample(s))</em></li>"
        for v in report.get("violations", [])
    ) or "<li>—</li>"

    evidence_cells = "".join(
        f"<figure><img src='{e(ev.get('thumbnail_url'))}' alt='Evidence {e(ev.get('inspection_id'))}'>"
        f"<figcaption>{e(ev.get('inspection_id'))}<br>{e(ev.get('timestamp'))}</figcaption></figure>"
        for ev in report.get("evidence", [])[:12]
    ) or "<p>—</p>"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Violation Report {e(report.get('session_id'))} — {e(report.get('manufacturer'))}</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; color:#111; margin:0; padding:36px; background:#f4f4f6; }}
  .sheet {{ max-width:900px; margin:0 auto; background:#fff; padding:40px; border:1px solid #d7d7de; }}
  h1 {{ font-size:20px; margin:0 0 4px; letter-spacing:.3px; }}
  .sub {{ color:#555; font-size:12.5px; margin-bottom:22px; }}
  .seized {{ display:inline-block; background:#b91c1c; color:#fff; font-weight:700; font-size:12px;
             padding:5px 12px; border-radius:3px; letter-spacing:.08em; }}
  table {{ width:100%; border-collapse:collapse; margin:12px 0 22px; font-size:13px; }}
  th, td {{ border:1px solid #d7d7de; padding:8px 10px; text-align:left; }}
  th {{ background:#f0f0f4; font-size:11px; text-transform:uppercase; letter-spacing:.05em; }}
  h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:.06em; color:#333;
        border-bottom:2px solid #111; padding-bottom:6px; margin:26px 0 10px; }}
  .grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:8px 24px; font-size:13px; }}
  .grid div span {{ color:#666; display:inline-block; min-width:150px; }}
  figure {{ display:inline-block; margin:0 12px 12px 0; width:150px; vertical-align:top; }}
  figure img {{ width:150px; height:110px; object-fit:cover; border:1px solid #d7d7de; }}
  figcaption {{ font-size:10px; color:#555; margin-top:4px; }}
  .foot {{ margin-top:30px; font-size:11px; color:#666; border-top:1px solid #d7d7de; padding-top:12px; }}
</style></head>
<body><div class="sheet">
  <h1>Statutory Violation Report</h1>
  <div class="sub">Issued under the {e(report.get('legal_basis'))}</div>
  <p><span class="seized">SEIZURE CONFIRMED</span></p>

  <h2>Addressed To</h2>
  <div class="grid">
    <div><span>Manufacturer</span> <strong>{e(report.get('manufacturer'))}</strong></div>
    <div><span>Brand</span> {e(report.get('brand'))}</div>
    <div><span>Registered contact</span> {e(report.get('recipient_email'))}</div>
    <div><span>Attention</span> {e(report.get('recipient_name'))}</div>
  </div>

  <h2>Inspection Particulars</h2>
  <div class="grid">
    <div><span>Inspection ID</span> <strong>{e(report.get('session_id'))}</strong></div>
    <div><span>Date &amp; time</span> {e(report.get('inspection_date'))}</div>
    <div><span>Location</span> {e(report.get('inspection_location'))}</div>
    <div><span>Inspection type</span> {e(report.get('inspection_type'))}</div>
    <div><span>Officer</span> {e(report.get('officer_name'))} ({e(report.get('officer_id'))})</div>
    <div><span>Jurisdiction</span> {e(report.get('jurisdiction'))}</div>
    <div><span>Seizure recorded</span> {e(report.get('seized_at'))}</div>
    <div><span>Report generated</span> {e(report.get('generated_at'))}</div>
  </div>

  <h2>Products Inspected ({e(report.get('products_inspected'))})</h2>
  <table>
    <thead><tr><th>Product</th><th>Category</th><th>Total stock</th><th>Samples</th><th>Compliant</th><th>Non-compliant</th></tr></thead>
    <tbody>{product_rows}</tbody>
  </table>

  <h2>Violations Detected</h2>
  <ul>{violation_rows}</ul>

  <h2>Evidence</h2>
  {evidence_cells}

  <div class="foot">
    Samples inspected: {e(report.get('samples_inspected'))} &nbsp;|&nbsp;
    Compliant: {e(report.get('compliant_samples'))} &nbsp;|&nbsp;
    Non-compliant: {e(report.get('non_compliant_samples'))}<br>
    Generated automatically by TRACE AI on seizure confirmation. Compliance verdicts are produced by the
    statutory rule engine from the officer's on-site package scans.
  </div>
</div></body></html>"""


def write_violation_report_html(report: Dict[str, Any], report_id: str) -> str:
    """Persist the notice next to the existing compliance certificates."""
    path = Path(REPORTS_DIR) / f"violation_{report_id}.html"
    try:
        path.write_text(render_violation_report_html(report), encoding="utf-8")
    except OSError:
        return ""
    return str(path)
