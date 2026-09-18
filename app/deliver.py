from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from urllib.parse import urlencode

import httpx


def public_url() -> str:
    return (os.environ.get("PUBLIC_URL") or "http://127.0.0.1:8790").rstrip("/")


def mail_ready() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def sms_ready() -> bool:
    return bool(os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN") and os.environ.get("TWILIO_FROM"))


def status() -> dict:
    return {"email": mail_ready(), "sms": sms_ready()}


def send_email(to: str, subject: str, body: str) -> dict:
    to = (to or "").strip()
    if not to or "@" not in to:
        return {"ok": False, "channel": "email", "error": "no recipient email"}
    if not mail_ready():
        return {"ok": False, "channel": "email", "error": "SMTP_HOST and SMTP_FROM are not set"}
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT") or 587)
    user = os.environ.get("SMTP_USER") or ""
    password = os.environ.get("SMTP_PASS") or ""
    frm = os.environ["SMTP_FROM"]
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            if port != 25:
                smtp.starttls()
                smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
        return {"ok": True, "channel": "email", "to": to}
    except Exception as e:
        return {"ok": False, "channel": "email", "error": str(e)[:300]}


def send_sms(to: str, body: str) -> dict:
    to = (to or "").strip()
    if not to:
        return {"ok": False, "channel": "sms", "error": "no phone"}
    if not sms_ready():
        return {"ok": False, "channel": "sms", "error": "Twilio is not configured"}
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    frm = os.environ["TWILIO_FROM"]
    digits = "".join(ch for ch in to if ch.isdigit() or ch == "+")
    if digits and not digits.startswith("+"):
        digits = "+1" + digits[-10:]
    try:
        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={"From": frm, "To": digits, "Body": body},
            timeout=20.0,
        )
        if r.status_code >= 300:
            return {"ok": False, "channel": "sms", "error": r.text[:300]}
        return {"ok": True, "channel": "sms", "to": digits, "sid": r.json().get("sid")}
    except Exception as e:
        return {"ok": False, "channel": "sms", "error": str(e)[:300]}


def deliver(channel: str, customer: dict, subject: str, body: str) -> dict:
    if channel == "sms":
        result = send_sms(customer.get("phone") or "", body)
        if result["ok"]:
            return result
        if customer.get("email"):
            fallback = send_email(customer["email"], subject, body)
            fallback["fallback_from"] = "sms"
            return fallback
        return result
    return send_email(customer.get("email") or "", subject, body)
