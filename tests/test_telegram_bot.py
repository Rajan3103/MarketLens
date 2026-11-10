"""Tests for marketlens/alerts/bot.py (the Telegram bot) and dashboards/telegram_panel.py (page 12).

No network: Telegram is never called (the python-telegram-bot parts get fake Update objects) and market data
is faked. Everything is stored in TEST_DATABASE_URL.
"""
import asyncio
import datetime as dt
import time
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from marketlens import config
from marketlens.alerts import bot
from marketlens.alerts import price_alerts as pa
from marketlens.alerts import telegram
from marketlens.broker.candles import IST
from marketlens.broker.session import LoginStatus
from marketlens.trading import live_runner as lr
from marketlens.trading import monitor as mon
from tests.test_live_runner import FWD_DAY, FakeLive, setup  # noqa: F401  (setup is a fixture)

CHAT = "987654"
PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "telegram_panel.py")
MARKET_TIME = dt.datetime.combine(FWD_DAY, dt.time(10, 0), IST)


@pytest.fixture
def core(setup, monkeypatch):
    """A bot for chat CHAT on the test database, with a deployed paper straddle."""
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", CHAT)
    lr.deploy(setup["sid"], url=setup["url"])
    c = bot.BotCore(url=setup["url"])
    c.setup = setup
    return c


# Rows of error_log (newest last).
def errors(url):
    return mon.error_log(days=3650, url=url).iloc[::-1]


# ---------------------------------------------------------------- who may use the bot

# Only TELEGRAM_CHAT_ID is answered; others are logged in error_log as a warning (and not told on Telegram).
def test_only_your_chat(core):
    assert core.allowed(CHAT) and core.allowed(int(CHAT)) and not core.allowed("111")
    core.refused(111, "stranger", "/squareoff")
    row = errors(core.url).iloc[-1]
    assert row["level"] == "WARNING" and "ignored a message from chat 111 (user stranger)" in row["message"]
    assert not any(n["source"] == "error_log" for n in telegram.recent_notifications(50, core.url))


# Without TELEGRAM_CHAT_ID nobody is answered.
def test_no_chat_id_means_nobody(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert bot.BotCore.allowed("") is False and bot.BotCore.allowed(CHAT) is False


# ---------------------------------------------------------------- commands

# /start, /help and an unknown command.
def test_help(core):
    assert "/squareoff" in core.handle("/start").text and "PAPER mode" in core.handle("/start").text
    assert core.handle("/help@MarketLensBot").text.startswith("Commands:")
    assert core.handle("/fly").text.startswith("Unknown command /fly.")


# /status: mode, kill switch, login, runner, data update and running strategies.
def test_status(core):
    text = core.handle("status").text
    assert "🔵 PAPER mode · AUTO_TRADE off" in text and "Kill switch: off" in text
    assert "Dhan login today: ❌ not checked today" in text and "Live runner: Never started" in text
    assert "Last data update: no daily prices yet" in text and "Live straddle: running" in text


# /strategies, /pause NAME and /resume NAME (by the start of the name; clear message otherwise).
def test_pause_and_resume(core):
    assert "Live straddle · PAPER · running" in core.handle("strategies").text
    assert core.handle("pause", ["live"]).text.startswith("⏸ Live straddle paused")
    assert lr.get_deployment(core.setup["sid"], core.url)["status"] == "paused"
    assert core.handle("resume", ["LIVE", "STRADDLE"]).text.startswith("▶️ Live straddle running")
    assert "No strategy matches 'iron'" in core.handle("pause", ["iron"]).text
    assert "Give the strategy name" in core.handle("pause").text


# /alerts, /addalert and /delalert.
def test_alert_commands(core):
    assert core.handle("alerts").text.startswith("No active price alerts")
    added = core.handle("addalert", ["nifty", "above", "25,500"]).text
    assert added.startswith("🔔 Alert #") and "NIFTY price above 25,500.00" in added
    alert_id = int(pa.list_alerts(url=core.url)["id"].iloc[0])
    assert f"#{alert_id} NIFTY price above 25,500.00" in core.handle("alerts").text
    assert "Use: /addalert" in core.handle("addalert", ["NIFTY", "sideways", "1"]).text
    assert "'lots' is not a number" in core.handle("addalert", ["NIFTY", "above", "lots"]).text
    assert core.handle("delalert", [str(alert_id)]).text == f"🗑 Alert #{alert_id} deleted."
    assert core.handle("delalert", [str(alert_id)]).text == f"No alert #{alert_id}."
    assert "Use: /delalert" in core.handle("delalert", ["x"]).text


# /stop turns the kill switch on; /resume_all asks, and only a fresh "yes" turns it off and resumes.
def test_stop_and_resume_all(core):
    core.handle("pause", ["live"])
    assert core.handle("stop").text.startswith("🛑 Kill switch ON")
    assert mon.kill_state(url=core.url)[0] is True
    ask = core.handle("resume_all")
    assert ask.text.startswith("Are you sure?") and ask.buttons[0][1] == ("❌ No", "resume_all:no")
    yes = ask.buttons[0][0][1]
    assert core.callback("resume_all:no").text == "OK, nothing was changed."
    old = f"resume_all:yes:{int(time.time()) - 600}"
    assert "older than 2 minutes" in core.callback(old).text and mon.kill_state(url=core.url)[0] is True
    done = core.callback(yes).text
    assert done.startswith("🟢 Kill switch OFF") and "▶️ Live straddle running" in done
    assert mon.kill_state(url=core.url)[0] is False
    assert lr.get_deployment(core.setup["sid"], core.url)["status"] == "running"


# /squareoff asks; "yes" closes everything, switches the kill switch on and stops every strategy.
def test_square_off(core):
    ask = core.handle("squareoff")
    assert "every paper position" in ask.text
    done = core.callback(ask.buttons[0][0][1]).text
    assert done.startswith("⛔ Done.") and "1 strategy(ies) stopped" in done
    assert lr.get_deployment(core.setup["sid"], core.url)["status"] == "stopped"
    assert mon.kill_state(url=core.url)[0] is True


# Live orders waiting for you show under /orders with Confirm / Reject buttons that work.
def test_waiting_orders_buttons(core, monkeypatch):
    fake = FakeLive()
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    service = lr.RunnerService(url=core.url, broker=fake, now=lambda: MARKET_TIME,
                               notify=lambda text, buttons=None: True,
                               login=lambda: LoginStatus("active", "ok", dt.datetime(2026, 10, 29, 9, 0, tzinfo=IST)))
    first = service.submit_signal(core.setup["sid"], "BUY", "NIFTY-Nov2026-FUT", 65, reason="entry", confirm=True)
    second = service.submit_signal(core.setup["sid"], "BUY", "NIFTY-Nov2026-FUT", 65, reason="again", confirm=True)
    core._service, core.now = service, lambda: MARKET_TIME
    reply = core.handle("orders")
    assert "Waiting for your Confirm (2)" in reply.text
    assert reply.buttons[0] == [(f"✅ Confirm {first.signal_id}", f"confirm:{first.signal_id}"),
                                (f"❌ Reject {first.signal_id}", f"reject:{first.signal_id}")]
    assert core.callback(f"confirm:{first.signal_id}").text.startswith(f"✅ Signal {first.signal_id}: SENT")
    assert fake.sent == [("NIFTY-Nov2026-FUT", "BUY", 65)]
    assert core.callback(f"reject:{second.signal_id}").text.startswith(f"❌ Signal {second.signal_id}: CANCELLED")
    assert "ERROR" in core.callback(f"reject:{second.signal_id}").text
    assert core.callback("launch:1").text == "Unknown button."


# /pnl, /positions, /report and /login.
def test_pnl_report_login(core, monkeypatch):
    monkeypatch.setattr(mon, "strategy_pnl_today", lambda mode, now, url=None: {core.setup["sid"]: -1200.0})
    assert core.handle("pnl").text.startswith("💰 Today: -Rs 1,200")
    assert core.handle("positions").text.startswith("No open positions.")
    report = core.handle("report").text
    assert "📊 MarketLens report" in report and "P&L today: -Rs 1,200" in report and "Kill switch: off" in report
    login = core.handle("login").text
    assert "not checked today" in login and "Daily Dhan login" in login


# A command that breaks says so briefly and writes the details to the error log.
def test_command_error_is_short(core, monkeypatch):
    monkeypatch.setattr(mon, "orders_today", lambda **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    assert core.handle("orders").text == "⚠️ /orders failed: RuntimeError. The details are in the error log."
    assert "db gone" in errors(core.url).iloc[-1]["message"]


# ---------------------------------------------------------------- the python-telegram-bot layer

class FakeMessage:
    """Records what the bot answers."""
    def __init__(self, text):
        self.text, self.replies = text, []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))


class FakeQuery:
    def __init__(self, data):
        self.data, self.answered, self.edits = data, False, []

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append(text)


# A fake Update / Context pair for one chat.
def update_for(core, chat_id, text=None, data=None):
    ns = type("NS", (), {})
    update, context = ns(), ns()
    update.effective_chat = type("Chat", (), {"id": chat_id})()
    update.effective_user = type("User", (), {"username": "me"})()
    update.effective_message = FakeMessage(text) if text is not None else None
    update.callback_query = FakeQuery(data) if data is not None else None
    context.args = (text or "").split()[1:]
    context.application = type("App", (), {"bot_data": {"core": core}})()
    return update, context


# Your chat gets an answer (with buttons, saved in notifications); a stranger gets nothing.
def test_handlers(core):
    update, context = update_for(core, CHAT, "/resume_all")
    asyncio.run(bot.on_command(update, context))
    text, markup = update.effective_message.replies[0]
    assert text.startswith("Are you sure?") and markup.inline_keyboard[0][1].callback_data == "resume_all:no"
    assert telegram.recent_notifications(1, core.url)[0]["source"] == "bot"

    stranger, context = update_for(core, 555, "/squareoff")
    asyncio.run(bot.on_command(stranger, context))
    assert stranger.effective_message.replies == [] and "chat 555" in errors(core.url).iloc[-1]["message"]

    tap, context = update_for(core, CHAT, data="resume_all:no")
    asyncio.run(bot.on_button(tap, context))
    assert tap.callback_query.answered and tap.callback_query.edits == ["OK, nothing was changed."]
    sneaky, context = update_for(core, 555, data=f"squareoff:yes:{int(time.time())}")
    asyncio.run(bot.on_button(sneaky, context))
    assert sneaky.callback_query.edits == [] and lr.get_deployment(core.setup["sid"], core.url)["status"] == "running"

    chat, context = update_for(core, CHAT, "hello")
    asyncio.run(bot.on_other(chat, context))
    assert chat.effective_message.replies[0][0].startswith("I only understand commands")


# The background loop writes a heartbeat (the daily report is the scheduler's job now).
def test_background_loop(core):
    async def stop(seconds):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(bot.background_loop(core, sleep=stop))
    assert lr.heartbeats(core.url)[bot.HEARTBEAT]["message"] == "answering commands"
    assert not any(n["event"] == "daily_report" for n in telegram.recent_notifications(20, core.url))


# The application has the commands, the buttons and the catch-all (built offline, nothing is started).
def test_build_application(core):
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler
    app = bot.build_application("123456:TEST-TOKEN", core)
    handlers = app.handlers[0]
    assert isinstance(handlers[0], CommandHandler) and set(handlers[0].commands) == set(bot.COMMANDS)
    assert isinstance(handlers[1], CallbackQueryHandler) and isinstance(handlers[2], MessageHandler)
    assert app.bot_data["core"] is core


# Without the token (or the chat id) the bot says what to do and stops.
def test_main_without_keys(monkeypatch, capsys):
    assert bot.main() == 1 and "TELEGRAM_BOT_TOKEN is not set" in capsys.readouterr().out
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:abc")
    assert bot.main() == 1 and "TELEGRAM_CHAT_ID is not set" in capsys.readouterr().out


# ---------------------------------------------------------------- page 12

@pytest.fixture
def page_db(db_url, db_session, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", db_url)       # the page uses the default database
    return db_url


# Bot status, test message (skipped without keys), notification switches, alerts and history.
def test_panel(page_db, monkeypatch):
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception
    assert any("Not started" in m.value for m in at.markdown)
    at.button(key="tg_test").click().run()
    assert "Not sent (skipped)" in at.error[0].value

    at.checkbox(key="tg_on_errors").uncheck()                  # a form: the change counts when Save is pressed
    at.button(key="FormSubmitter:tg_settings-💾 Save").click().run()
    assert telegram.notification_settings(page_db)["errors"] is False

    at.text_input(key="pa_symbol").input("RELIANCE").run()
    at.selectbox(key="pa_condition").select("BELOW").run()
    at.number_input(key="pa_target").set_value(1500.0).run()
    at.button(key="pa_add").click().run()
    assert at.success[-1].value.startswith("Alert #")
    assert pa.list_alerts(url=page_db)["description"].tolist() == ["RELIANCE price below 1,500.00"]

    monkeypatch.setattr(pa, "latest_quotes", lambda symbols: {"RELIANCE": (1_480.0, 1_510.0)})
    at.button(key="pa_check").click().run()
    assert "1 alert(s) triggered" in at.success[-1].value
    assert not pa.list_alerts(url=page_db)["is_active"].iloc[0]

    at.button(key="pa_delete").click().run()
    assert pa.list_alerts(url=page_db).empty
    history = at.dataframe[-1].value
    assert {"test", "price_alert"} <= set(history["event"])


# The page explains itself when the database is down.
def test_panel_without_database(monkeypatch):
    monkeypatch.setattr("marketlens.db.connection_status", lambda url=None: (False, "not running"))
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception and "database is not connected" in at.error[0].value
