"""Alert dispatch — a thin wrapper over an optional outbound webhook.

If `ALERT_WEBHOOK_URL` is unset, alerts are logged only (`TODO: REQUIRES API`
for push/email/SMS delivery — none of those are wired up in this build).
"""
from __future__ import annotations

import logging

import httpx

from config import get_settings

logger = logging.getLogger("quant_agent.alerts")


def send_alert(title: str, message: str, severity: str = "INFO") -> None:
    logger.log(
        logging.CRITICAL if severity in {"BREACH", "KILL_SWITCH"} else logging.WARNING if severity == "WARNING" else logging.INFO,
        "[%s] %s: %s", severity, title, message,
    )
    settings = get_settings()
    if not settings.alert_webhook_url:
        return
    try:
        httpx.post(settings.alert_webhook_url, json={"title": title, "message": message, "severity": severity}, timeout=5.0)
    except httpx.HTTPError as exc:
        logger.warning("Failed to deliver alert webhook: %s", exc)
