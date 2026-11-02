"""Send a message to your Telegram chat (module 12 will add bot commands).

    send_message("✅ Paper order filled: BUY 10 RELIANCE at 2,501.50")

Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env. Without them nothing is sent and the
log says "Telegram key not set"; callers never fail because of Telegram.
"""
import json
import urllib.error
import urllib.request

from marketlens import config
from marketlens.logger import get_logger

log = get_logger(__name__)
API = "https://api.telegram.org/bot{token}/sendMessage"


# True when both Telegram settings are filled in.
def is_configured() -> bool:
    return config.is_set(config.TELEGRAM_BOT_TOKEN) and config.is_set(config.TELEGRAM_CHAT_ID)


# Send one text message; returns True when Telegram accepted it.
# buttons = [("✅ Confirm", "confirm:12"), ...] adds tap buttons under the message (the bot gets the data).
def send_message(text: str, timeout: float = 10, buttons: list[tuple[str, str]] | None = None) -> bool:
    if not is_configured():
        log.info("Telegram key not set (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID): message not sent: %s", text[:80])
        return False
    message = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000], "disable_web_page_preview": True}
    if buttons:
        message["reply_markup"] = {"inline_keyboard": [[{"text": label, "callback_data": data}
                                                        for label, data in buttons]]}
    body = json.dumps(message).encode()
    request = urllib.request.Request(API.format(token=config.TELEGRAM_BOT_TOKEN), data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            ok = json.load(answer).get("ok", False)
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        log.warning("Telegram message failed: %s", err)       # the token is never written to the log
        return False
    if not ok:
        log.warning("Telegram did not accept the message")
    return bool(ok)
