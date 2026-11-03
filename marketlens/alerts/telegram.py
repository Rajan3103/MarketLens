"""Send a message to your Telegram chat and keep a copy of every message (module 12 will add bot commands).

    notify("✅ Paper order filled: BUY 10 RELIANCE at 2,501.50", source="order_service", event="order_filled")
    send_message("text")                      # the same, returns True when Telegram accepted it

Every message is saved in the notifications table with its status:
    sent     Telegram accepted it
    failed   Telegram refused it or could not be reached (the reason is in `error`)
    skipped  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set, so nothing was sent
Callers never fail because of Telegram or the database. The bot token is never written to the log.
"""
import datetime as dt
import json
import urllib.error
import urllib.request

from marketlens import config
from marketlens.logger import get_logger

log = get_logger(__name__)
API = "https://api.telegram.org/bot{token}/sendMessage"
SENT, FAILED, SKIPPED = "sent", "failed", "skipped"
MAX_LENGTH = 4000                     # Telegram allows 4096 characters


# True when both Telegram settings are filled in.
def is_configured() -> bool:
    return config.is_set(config.TELEGRAM_BOT_TOKEN) and config.is_set(config.TELEGRAM_CHAT_ID)


# POST one message to Telegram; returns Telegram's answer as a dict (tests replace this).
def _post(message: dict, timeout: float) -> dict:
    request = urllib.request.Request(API.format(token=config.TELEGRAM_BOT_TOKEN), data=json.dumps(message).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.load(answer)


# Try to send one message; returns (status, error): ("sent", None), ("failed", why) or ("skipped", why).
def deliver(text: str, buttons: list[tuple[str, str]] | None = None, timeout: float = 10) -> tuple[str, str | None]:
    """buttons = [("✅ Confirm", "confirm:12"), ...] adds tap buttons under the message (the bot gets the data)."""
    if not is_configured():
        log.info("Telegram key not set (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID): message not sent: %s", text[:80])
        return SKIPPED, "Telegram key not set (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)"
    message = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:MAX_LENGTH], "disable_web_page_preview": True}
    if buttons:
        message["reply_markup"] = {"inline_keyboard": [[{"text": label, "callback_data": data}
                                                        for label, data in buttons]]}
    try:
        answer = _post(message, timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        why = _without_token(f"Telegram could not be reached: {err}")
        log.warning(why)
        return FAILED, why
    if not answer.get("ok", False):
        why = f"Telegram did not accept the message: {answer.get('description', 'no reason given')}"
        log.warning(why)
        return FAILED, why
    return SENT, None


# An error text with the bot token taken out (urllib errors can contain the address).
def _without_token(text: str) -> str:
    token = config.TELEGRAM_BOT_TOKEN
    return text.replace(token, "***") if token else text


# Keep a copy of one message in the notifications table. Never raises; returns the row id (or None).
def save_notification(text: str, status: str, error: str | None = None, source: str | None = None,
                      event: str | None = None, url: str | None = None, channel: str = "telegram") -> int | None:
    try:
        from marketlens.db import get_session
        from marketlens.models import Notification
        with get_session(url) as session:
            row = Notification(channel=channel, message=text, status=status, error=error, source=source, event=event,
                               sent_at=dt.datetime.now(dt.timezone.utc) if status == SENT else None)
            session.add(row)
            session.commit()
            return row.id
    except Exception as err:                    # no database: the log still has the message
        log.warning("Could not save the notification: %s", err)
        return None


# Send a message and save it (sent / failed / skipped); returns True when Telegram accepted it.
def notify(text: str, buttons: list[tuple[str, str]] | None = None, source: str | None = None,
           event: str | None = None, url: str | None = None, timeout: float = 10) -> bool:
    status, error = deliver(text, buttons, timeout)
    save_notification(text, status, error, source, event, url)
    return status == SENT


# The old name, kept for callers: send and save one message; True when Telegram accepted it.
def send_message(text: str, timeout: float = 10, buttons: list[tuple[str, str]] | None = None,
                 source: str | None = None, event: str | None = None, url: str | None = None) -> bool:
    return notify(text, buttons, source, event, url, timeout)


# The newest saved messages (for a page or a test): [{id, created_at, status, source, event, message, error}].
def recent_notifications(limit: int = 50, url: str | None = None) -> list[dict]:
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import Notification
    with get_session(url) as session:
        rows = session.scalars(select(Notification).order_by(Notification.id.desc()).limit(limit)).all()
        return [{"id": r.id, "created_at": r.created_at, "status": r.status, "source": r.source, "event": r.event,
                 "message": r.message, "error": r.error} for r in rows]
