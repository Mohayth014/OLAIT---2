"""
Dispatch channel for manufacturer violation reports.

Default mode is SIMULATED: the report is generated, the registered recipient is
resolved, and the send is recorded with a timestamp -- but nothing leaves the
machine. That is the honest default for a prototype demo: it behaves identically
on stage, needs no credentials or connectivity, and cannot be mistaken for a real
integration with a manufacturer.

Set TRACE_DISPATCH_MODE=smtp plus the SMTP_* variables below to send for real to a
test inbox (Mailtrap, a Gmail app-password account, etc.). The calling code is the
same either way -- only this module knows the difference.

    TRACE_DISPATCH_MODE = simulated | smtp     (default: simulated)
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    SMTP_STARTTLS = 1 | 0                      (default: 1)
"""
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Dict, Optional


def dispatch_mode() -> str:
    return (os.environ.get("TRACE_DISPATCH_MODE") or "simulated").strip().lower()


def _plain_text_summary(report: Dict[str, Any]) -> str:
    lines = [
        "STATUTORY VIOLATION REPORT",
        f"Issued under the {report.get('legal_basis')}",
        "",
        f"Manufacturer      : {report.get('manufacturer')}",
        f"Inspection ID     : {report.get('session_id')}",
        f"Location          : {report.get('inspection_location')}",
        f"Inspection date   : {report.get('inspection_date')}",
        f"Officer           : {report.get('officer_name')} ({report.get('officer_id')})",
        f"Jurisdiction      : {report.get('jurisdiction')}",
        f"Seizure status    : {report.get('seizure_status')} at {report.get('seized_at')}",
        "",
        f"Products inspected: {report.get('products_inspected')}",
        f"Samples inspected : {report.get('samples_inspected')} "
        f"(compliant {report.get('compliant_samples')}, non-compliant {report.get('non_compliant_samples')})",
        "",
        "Violations detected:",
    ]
    for v in report.get("violations", []) or []:
        lines.append(f"  - [{v.get('product_name')}] {v.get('violation')} (x{v.get('sample_count')})")
    if not report.get("violations"):
        lines.append("  - (none recorded)")
    lines += ["", "Generated automatically by TRACE AI on seizure confirmation."]
    return "\n".join(lines)


def _send_smtp(recipient: str, subject: str, text_body: str, html_body: str) -> Dict[str, Any]:
    host = os.environ.get("SMTP_HOST")
    if not host:
        raise RuntimeError("TRACE_DISPATCH_MODE=smtp but SMTP_HOST is not configured.")
    port = int(os.environ.get("SMTP_PORT") or 587)
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM") or user or "trace-ai@localhost"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(host, port, timeout=20) as server:
        if (os.environ.get("SMTP_STARTTLS") or "1") == "1":
            server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)
    return {"status": "SENT", "detail": f"Delivered via SMTP {host}:{port} to {recipient}"}


def dispatch_violation_report(
    report: Dict[str, Any],
    html_body: str,
    recipient: Optional[str],
) -> Dict[str, Any]:
    """Deliver one violation report. Never raises -- a delivery failure is recorded
    as an outcome on the report, since the seizure itself has already happened and
    must not be rolled back by a mail problem.

    Returns {status, detail, sent_at, mode}: status is SENT, SIMULATED,
    NO_CONTACT or FAILED.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode = dispatch_mode()
    subject = (
        f"Statutory Violation Report — {report.get('manufacturer')} — "
        f"Inspection {report.get('session_id')}"
    )

    if not recipient:
        return {
            "status": "NO_CONTACT",
            "detail": "No registered contact on file for this manufacturer; report generated but not dispatched.",
            "sent_at": None,
            "mode": mode,
        }

    if mode == "smtp":
        try:
            result = _send_smtp(recipient, subject, _plain_text_summary(report), html_body)
            return {**result, "sent_at": now, "mode": mode}
        except Exception as e:
            return {"status": "FAILED", "detail": f"SMTP delivery failed: {e}", "sent_at": None, "mode": mode}

    # Simulated: record exactly what would have been sent, deliver nothing.
    return {
        "status": "SIMULATED",
        "detail": (
            f"Simulated dispatch to {recipient} — subject \"{subject}\". "
            "No message left this machine (set TRACE_DISPATCH_MODE=smtp to send for real)."
        ),
        "sent_at": now,
        "mode": mode,
    }
