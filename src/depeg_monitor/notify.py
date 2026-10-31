"""Outbound notifications. This module cannot sign or submit a transaction."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


@dataclass
class TelegramNotifier:
    token: str
    chat_id: str
    name: str = "telegram"

    def send(self, text: str) -> bool:
        # The bot token is a URL path segment. Log only the status, never the URL.
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text[:4000],
                        "disable_web_page_preview": True,
                    },
                )
        except httpx.HTTPError as exc:
            log.warning("telegram transport failed (%s)", exc.__class__.__name__)
            return False
        if response.status_code != 200:
            log.warning("telegram rejected a message (HTTP %s)", response.status_code)
            return False
        return True


@dataclass
class WebhookNotifier:
    url: str
    name: str = "webhook"
    discord: bool = False

    def send(self, text: str) -> bool:
        if self.discord:
            payload: dict[str, object] = {"content": text[:1900]}
        else:
            payload = {"event": "depeg-monitor", "text": text}
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(self.url, json=payload)
        except httpx.HTTPError as exc:
            log.warning("%s transport failed (%s)", self.name, exc.__class__.__name__)
            return False
        if response.status_code >= 300:
            log.warning("%s rejected a message (HTTP %s)", self.name, response.status_code)
            return False
        return True


def build_notifiers() -> list[TelegramNotifier | WebhookNotifier]:
    """Build notifiers from the environment. Missing optional URLs are skipped."""
    notifiers: list[TelegramNotifier | WebhookNotifier] = []
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token and chat_id:
        notifiers.append(TelegramNotifier(token=token, chat_id=chat_id))
    elif token or chat_id:
        log.warning("set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or neither")
    discord = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if discord:
        notifiers.append(WebhookNotifier(url=discord, name="discord", discord=True))
    generic = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if generic:
        notifiers.append(WebhookNotifier(url=generic, name="webhook", discord=False))
    return notifiers


def deliver(notifiers: list[TelegramNotifier | WebhookNotifier], text: str) -> bool:
    """True when at least one channel accepted the message."""
    return any(notifier.send(text) for notifier in notifiers)
