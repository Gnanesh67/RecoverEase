"""
Email service — Gmail SMTP via aiosmtplib.
Works for ANY recipient domain. Free, no API key needed.
Requires: SMTP_USER (your Gmail) + SMTP_PASSWORD (Gmail App Password).

Setup (one-time):
  1. Enable 2-Step Verification on your Google account.
  2. Go to https://myaccount.google.com/apppasswords
  3. Create an App Password for "Mail" → copy the 16-char code.
  4. Set SMTP_USER=you@gmail.com  SMTP_PASSWORD=<16-char code> in .env
"""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as html_escape

import aiosmtplib
import httpx

from app.core.config import settings
from app.core.logger import logger

if settings.smtp_enabled:
    logger.info("smtp_ready", user=settings.smtp_user, host=settings.smtp_host)
else:
    logger.warning(
        "smtp_disabled",
        msg="SMTP_USER / SMTP_PASSWORD not set — emails will be logged only. "
            "Add Gmail credentials to .env to enable real sending.",
    )


def _build_message(to: str, subject: str, html_body: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_user}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    return msg


async def send_email_with_diagnostics(to: str, subject: str, html_body: str) -> dict:
    """Send email via Brevo API, Resend API, or Gmail SMTP, returning full diagnostics."""
    diag = {
        "success": False,
        "recipient": to,
        "env_detected": {
            "has_brevo_api_key": bool(settings.brevo_api_key),
            "has_resend_api_key": bool(settings.resend_api_key),
            "has_smtp_user": bool(settings.smtp_user),
            "has_smtp_password": bool(settings.smtp_password),
        },
        "provider_attempted": None,
        "details": None,
        "error": None,
    }

    # 1. Brevo HTTP API (Port 443 — works on Render free tier to ANY recipient)
    if settings.brevo_api_key:
        diag["provider_attempted"] = "brevo"
        sender_email = settings.brevo_sender_email or settings.smtp_user or "alerts@recoverease.dev"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={
                        "accept": "application/json",
                        "api-key": settings.brevo_api_key,
                        "content-type": "application/json",
                    },
                    json={
                        "sender": {
                            "name": settings.smtp_from_name,
                            "email": sender_email,
                        },
                        "to": [{"email": to}],
                        "subject": subject,
                        "htmlContent": html_body,
                    },
                )
                if resp.status_code in (200, 201):
                    logger.info("email_sent_brevo", to=to, subject=subject)
                    diag["success"] = True
                    diag["details"] = resp.json()
                    return diag
                else:
                    logger.error("brevo_failed", to=to, status=resp.status_code, body=resp.text)
                    diag["error"] = f"Brevo HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            logger.error("brevo_exception", to=to, error=str(e))
            diag["error"] = f"Brevo exception: {str(e)}"

    # 2. Resend HTTP API (Port 443 — works on Render free tier)
    if settings.resend_api_key:
        diag["provider_attempted"] = "resend"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {settings.resend_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": f"{settings.smtp_from_name} <onboarding@resend.dev>",
                        "to": [to],
                        "subject": subject,
                        "html": html_body,
                    },
                )
                if resp.status_code in (200, 201):
                    logger.info("email_sent_resend", to=to, subject=subject)
                    diag["success"] = True
                    diag["details"] = resp.json()
                    return diag
                else:
                    logger.error("resend_failed", to=to, status=resp.status_code, body=resp.text)
                    err_text = resp.text
                    if "testing emails to your own email address" in err_text:
                        diag["error"] = (
                            f"Resend Restriction: {err_text}. "
                            "Resend free tier only sends to the email address used to create the Resend account. "
                            "To send to any recipient, add BREVO_API_KEY in Render."
                        )
                    else:
                        diag["error"] = f"Resend HTTP {resp.status_code}: {err_text}"
        except Exception as e:
            logger.error("resend_exception", to=to, error=str(e))
            diag["error"] = f"Resend exception: {str(e)}"

    # 3. Gmail SMTP (fallback for local dev or paid Render with open ports)
    if settings.smtp_user and settings.smtp_password:
        diag["provider_attempted"] = "smtp"
        try:
            msg = _build_message(to, subject, html_body)
            use_tls = settings.smtp_port == 465
            start_tls = settings.smtp_port == 587
            await aiosmtplib.send(
                msg,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                use_tls=use_tls,
                start_tls=start_tls,
                timeout=8.0,
            )
            logger.info("email_sent_smtp", to=to, subject=subject)
            diag["success"] = True
            diag["details"] = "Email sent successfully via Gmail SMTP."
            return diag
        except Exception as e:
            logger.error("email_failed", to=to, subject=subject, error=str(e))
            err_msg = str(e)
            if "Timed out" in err_msg or "timeout" in err_msg.lower():
                diag["error"] = (
                    f"SMTP timeout: {err_msg}. "
                    "Render Free Tier blocks outbound SMTP ports 25, 465, and 587. "
                    "Please set BREVO_API_KEY or RESEND_API_KEY in Render Environment to send over HTTPS port 443."
                )
            else:
                diag["error"] = f"SMTP error: {err_msg}"

    if not diag["provider_attempted"]:
        diag["error"] = (
            "No email service configured. Please add BREVO_API_KEY, RESEND_API_KEY, "
            "or SMTP_USER/SMTP_PASSWORD in Render Dashboard -> Environment."
        )

    return diag


async def _send(to: str, subject: str, html_body: str) -> bool:
    """Core send — returns True on success, never raises."""
    result = await send_email_with_diagnostics(to, subject, html_body)
    return bool(result.get("success", False))


async def send_caregiver_alert(
    caregiver_email: str,
    patient_name: str,
    severity: str,
    details: str,
) -> bool:
    """Send an escalation alert to a caregiver or doctor."""
    safe_name = html_escape(patient_name)
    safe_sev = html_escape(severity)
    safe_det = html_escape(details)
    color = {"CRITICAL": "#dc2626", "HIGH": "#ea580c", "MEDIUM": "#d97706", "LOW": "#16a34a"}.get(severity.upper(), "#6b7280")
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:24px;border:1px solid #e5e7eb;border-radius:12px">
      <h2 style="color:{color};margin:0 0 12px">Recovery Companion Alert</h2>
      <p><strong>Patient:</strong> {safe_name}</p>
      <p><strong>Severity:</strong> <span style="color:{color}">{safe_sev}</span></p>
      <p><strong>Details:</strong> {safe_det}</p>
      <p style="color:#6b7280;font-size:13px">Please check on your patient or contact them directly.</p>
    </div>"""
    return await _send(
        caregiver_email,
        f"Recovery Alert — {patient_name} ({severity})",
        html,
    )


async def send_sos_alert(
    doctor_email: str,
    patient_name: str,
    notes: str | None = None,
) -> bool:
    """Send an SOS alert to the actively treating doctor."""
    safe_name = html_escape(patient_name)
    safe_notes = html_escape(notes or "No additional notes provided")
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:24px;border:2px solid #dc2626;border-radius:12px">
      <h2 style="color:#dc2626;margin:0 0 12px">🚨 SOS Alert</h2>
      <p><strong>Patient:</strong> {safe_name}</p>
      <p><strong>Notes:</strong> {safe_notes}</p>
      <p style="color:#dc2626;font-weight:bold">This patient needs immediate attention. Please respond now.</p>
    </div>"""
    return await _send(
        doctor_email,
        f"🚨 SOS — {patient_name} needs immediate attention",
        html,
    )


async def send_sos_confirmation_to_patient(
    patient_email: str,
    patient_name: str,
    notes: str | None = None,
) -> bool:
    """Send an SOS confirmation to the patient confirming their alert was dispatched."""
    safe_name = html_escape(patient_name)
    safe_notes = html_escape(notes or "No additional notes provided")
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:24px;border:2px solid #ef4444;border-radius:12px">
      <h2 style="color:#ef4444;margin:0 0 12px">🚨 SOS Emergency Alert Dispatched</h2>
      <p>Hello <strong>{safe_name}</strong>,</p>
      <p>Your emergency SOS alert has been recorded and dispatched to your doctor and caregiver.</p>
      <p><strong>Your Notes:</strong> {safe_notes}</p>
      <p style="color:#4b5563;font-size:13px">If you are in immediate life-threatening danger, please dial your local emergency services (e.g. 112 or 911).</p>
    </div>"""
    return await _send(
        patient_email,
        "🚨 SOS Alert Dispatched — RecoverEase",
        html,
    )
