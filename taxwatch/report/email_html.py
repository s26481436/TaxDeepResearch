"""Send change report via email."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from taxwatch.config import get_settings

logger = logging.getLogger(__name__)


def send_report_email(report_path: Path):
    settings = get_settings()
    if not settings.smtp_host or not settings.email_to:
        logger.warning("Email not configured (SMTP_HOST or EMAIL_TO missing), skipping")
        return

    content = report_path.read_text(encoding="utf-8")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"TaxWatch 稅法異動報告 — {report_path.stem}"
    msg["From"] = settings.email_from
    msg["To"] = settings.email_to

    msg.attach(MIMEText(content, "plain", "utf-8"))

    if report_path.suffix == ".html":
        msg.attach(MIMEText(content, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            if settings.smtp_user:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.email_from, settings.email_to.split(","), msg.as_string())
        logger.info("Report email sent to %s", settings.email_to)
    except Exception:
        logger.exception("Failed to send report email")
        raise
