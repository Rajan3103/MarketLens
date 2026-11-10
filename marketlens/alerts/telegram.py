"""Send a message to your Telegram chat and keep a copy of every message (module 12 will add bot commands).

    notify("✅ Paper order filled: BUY 10 RELIANCE at 2,501.50", source="order_service", event="order_filled")
    send_message("text")                      # the same, returns True when Telegram accepted it

Every message is saved in the notifications table with its status:
    sent     Telegram accepted it
    failed   Telegram refused it or could not be reached (the reason is in `error`)
    skipped  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set, or that kind of message is switched off on the
             Telegram panel (page 12), so nothing was sent
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
SETTINGS_KEY = "telegram_notifications"
# Kinds of message you can switch on / off on the Telegram panel, and the events in each
CATEGORIES = {
    "signals": ("Signals and strategy events", ("signal", "signal_blocked", "strategy_paused", "strategy_stopped")),
    "fills": ("Orders placed, filled, rejected, cancelled",
              ("order_placed", "order_filled", "order_rejected", "order_cancelled")),
    "sl_hits": ("Stop-loss hits", ("sl_hit",)),
    "errors": ("Errors", ("error",)),
    "daily_report": ("Daily report (15:50)", ("daily_report",)),
    "webhook": ("Webhook alerts (TradingView / Chartink)", ("webhook_alert",)),
}
# Always sent, whatever the panel says: you must see these
ALWAYS_ON = ("confirm_request", "kill_switch", "price_alert", "login_reminder", "test")


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


# ---------------------------------------------------------------- which messages are on

# The category of an event ("fills", "errors" ...), or None when it cannot be switched off.
def category_of(event: str | None) -> str | None:
    if event in ALWAYS_ON:
        return None
    return next((name for name, (_, events) in CATEGORIES.items() if event in events), None)


# Which categories are on: {"signals": True, ...}. Everything is on until you change it (or without a database).
def notification_settings(url: str | None = None) -> dict:
    on = dict.fromkeys(CATEGORIES, True)
    try:
        from marketlens.db import get_session
        from marketlens.models import AppSetting
        with get_session(url) as session:
            row = session.get(AppSetting, SETTINGS_KEY)
            if row is not None:
                on.update({k: bool(v) for k, v in (row.value or {}).items() if k in CATEGORIES})
    except Exception as err:
        log.warning("Could not read the notification settings (all on): %s", err)
    return on


# Switch categories on / off, e.g. {"errors": False}; returns the new settings.
def set_notification_settings(changes: dict, url: str | None = None) -> dict:
    from sqlalchemy.dialects.postgresql import insert

    from marketlens.db import get_session
    from marketlens.models import AppSetting
    unknown = set(changes) - set(CATEGORIES)
    if unknown:
        raise ValueError(f"Unknown notification type(s): {', '.join(sorted(unknown))}")
    value = {**notification_settings(url), **{k: bool(v) for k, v in changes.items()}}
    with get_session(url) as session:
        session.execute(insert(AppSetting).values(key=SETTINGS_KEY, value=value)
                        .on_conflict_do_update(index_elements=["key"], set_={"value": value}))
        session.commit()
    log.info("Telegram notifications: %s", ", ".join(f"{k} {'on' if v else 'off'}" for k, v in value.items()))
    return value


# ---------------------------------------------------------------- sending

# Send a message and save it (sent / failed / skipped); returns True when Telegram accepted it.
# A message whose kind is switched off on the panel is saved as skipped and not sent.
def notify(text: str, buttons: list[tuple[str, str]] | None = None, source: str | None = None,
           event: str | None = None, url: str | None = None, timeout: float = 10) -> bool:
    category = category_of(event)
    if category and not notification_settings(url)[category]:
        save_notification(text, SKIPPED, f"Switched off on the Telegram panel ({CATEGORIES[category][0]})",
                          source, event, url)
        return False
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
