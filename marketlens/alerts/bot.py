"""The MarketLens Telegram bot (module 12): run it with  python -m marketlens.alerts.bot

Commands (only from your own chat, TELEGRAM_CHAT_ID; every other chat is ignored and logged):
    /status  /positions  /orders  /pnl  /strategies  /report  /login  /alerts
    /pause NAME  /resume NAME         one deployed strategy
    /addalert SYMBOL above|below LEVEL  /delalert ID
    /stop        kill switch ON (no new orders today)
    /resume_all  kill switch OFF and paused strategies running again (asks "are you sure")
    /squareoff   close every position, kill switch on, stop every strategy (asks to confirm)
It also answers the ✅ Confirm / ❌ Reject buttons under live orders that wait for you, sends the daily
report at 15:45 (if switched on on page 12) and writes a heartbeat every minute (page 11's status card).

The work is done by BotCore (plain Python, easy to test); the rest connects it to python-telegram-bot.
Paper or live follows TRADING_MODE in .env; the bot cannot change it.
"""
import asyncio
import datetime as dt
import sys
import time
from dataclasses import dataclass, field

from marketlens import config
from marketlens.broker.candles import IST
from marketlens.logger import get_logger, record_error

log = get_logger(__name__)
HEARTBEAT = "telegram_bot"
CONFIRM_SECONDS = 120                     # a "yes, square off" button older than this is refused
REPORT_AT = dt.time(15, 45)
MAX_LINES = 30                            # rows shown per table (Telegram messages are short)
COMMANDS = {
    "start": "Hello and the list of commands",
    "help": "The list of commands",
    "status": "Mode, Dhan login, kill switch, running strategies, last data update",
    "positions": "Open positions",
    "orders": "Today's orders, and the ones waiting for your Confirm",
    "pnl": "Today's P&L against the loss limits",
    "strategies": "Deployed strategies and their status",
    "pause": "/pause NAME: no new orders for that strategy",
    "resume": "/resume NAME: run a paused strategy again",
    "alerts": "Active price alerts",
    "addalert": "/addalert SYMBOL above|below LEVEL, e.g. /addalert NIFTY above 25500",
    "delalert": "/delalert ID: delete a price alert",
    "stop": "Kill switch ON: no new orders today",
    "resume_all": "Kill switch OFF and paused strategies running again (asks first)",
    "squareoff": "Close every position, kill switch on, stop every strategy (asks first)",
    "report": "Today's report",
    "login": "Today's Dhan login and how to renew it",
}


@dataclass
class Reply:
    """What the bot answers: text, and rows of tap buttons [(label, data), ...]."""
    text: str
    buttons: list = field(default_factory=list)


# Rupees with commas, e.g. "Rs 1,250" / "-Rs 300".
def rs(value) -> str:
    if value is None:
        return "-"
    return f"{'-' if value < 0 else ''}Rs {abs(float(value)):,.0f}"


class BotCore:
    """Every command as a plain function: (args) -> Reply. url / service / now can be replaced in tests."""

    def __init__(self, url: str | None = None, service=None, now=None):
        self.url = url
        self._service = service
        self.now = now or (lambda: dt.datetime.now(IST))

    # The order service (kill switch, confirm / reject, square off).
    def service(self):
        if self._service is not None:
            return self._service
        from marketlens.trading.order_service import OrderService
        return OrderService(url=self.url)

    # ---------------------------------------------------------------- who may talk to the bot

    # True only for your own chat (TELEGRAM_CHAT_ID).
    @staticmethod
    def allowed(chat_id) -> bool:
        return config.is_set(config.TELEGRAM_CHAT_ID) and str(chat_id) == str(config.TELEGRAM_CHAT_ID).strip()

    # Log a message from another chat (and keep it in error_log as a warning); nothing is answered.
    def refused(self, chat_id, user: str | None, text: str | None):
        record_error(__name__, f"Telegram bot: ignored a message from chat {chat_id} (user {user or '?'}): "
                               f"{(text or '')[:100]!r}", {"chat_id": str(chat_id), "user": user}, url=self.url,
                     level="WARNING")

    # ---------------------------------------------------------------- commands

    # Run one command; unknown commands get the help text. Errors become a short message.
    def handle(self, command: str, args: list[str] | None = None) -> Reply:
        command = command.lower().lstrip("/").split("@")[0]
        method = getattr(self, f"cmd_{command}", None) if command in COMMANDS else None
        if method is None:
            return Reply(f"Unknown command /{command}.\n\n" + self.help_text())
        try:
            return method(list(args or []))
        except ValueError as err:                       # a mistake in what you asked
            return Reply(f"⚠️ {err}")
        except Exception as err:
            record_error(__name__, f"Telegram bot /{command} failed: {type(err).__name__}: {err}", url=self.url)
            return Reply(f"⚠️ /{command} failed: {type(err).__name__}. The details are in the error log.")

    # The list of commands.
    @staticmethod
    def help_text() -> str:
        return "Commands:\n" + "\n".join(f"/{name} - {text}" for name, text in COMMANDS.items())

    # /start
    def cmd_start(self, args) -> Reply:
        mode = config.TRADING_MODE.upper()
        return Reply(f"👋 MarketLens bot, {mode} mode. Only this chat can use it.\n\n" + self.help_text())

    # /help
    def cmd_help(self, args) -> Reply:
        return Reply(self.help_text())

    # /status: mode, login, kill switch, running strategies, last data update.
    def cmd_status(self, args) -> Reply:
        from marketlens.trading import live_runner as lr
        from marketlens.trading import monitor as mon
        killed, why = mon.kill_state(url=self.url, now=self.now())
        login = mon.login_today(self.url, self.now())
        deps = lr.list_deployments(self.url)
        active = deps[deps["status"].isin(lr.ACTIVE)]
        beats = lr.heartbeats(self.url)
        runner_state, runner_text = mon.heartbeat_state(beats.get(lr.RUNNER), now=self.now())
        lines = [f"{'🔴 LIVE' if config.TRADING_MODE == 'live' else '🔵 PAPER'} mode · AUTO_TRADE "
                 f"{'on' if config.AUTO_TRADE else 'off'}",
                 f"Kill switch: {'🛑 ON (' + why + ')' if killed else 'off'}",
                 f"Dhan login today: {_login_line(login)}",
                 f"Live runner: {runner_text}",
                 f"Last data update: {self._last_data_update()}",
                 f"Running strategies ({len(active)}):"]
        lines += [f"  {_icon(d.status)} {d.name}: {d.status}, today {rs(d.pnl)}" for d in active.itertuples()] or ["  none"]
        return Reply("\n".join(lines))

    # Newest stored daily price and option snapshot.
    def _last_data_update(self) -> str:
        from sqlalchemy import func, select

        from marketlens.db import get_session
        from marketlens.models import DailyPrice, OptionQuote
        with get_session(self.url) as session:
            prices = session.scalar(select(func.max(DailyPrice.date)))
            options = session.scalar(select(func.max(OptionQuote.ts)))
        parts = [f"daily prices {prices:%d-%b-%Y}" if prices else "no daily prices yet"]
        if options:
            parts.append(f"option chain {options.astimezone(IST):%d-%b %H:%M}")
        return ", ".join(parts)

    # /positions
    def cmd_positions(self, args) -> Reply:
        from marketlens.trading import monitor as mon
        table, note = mon.open_positions(url=self.url)
        if table.empty:
            return Reply(f"No open positions. ({note})")
        lines = [f"{r.symbol} {int(r.qty):+d} @ {r.avg_price:,.2f} · open {rs(r.unrealized_pnl)} ({r.strategy})"
                 for r in table.head(MAX_LINES).itertuples()]
        return Reply(f"📂 Open positions ({len(table)}):\n" + "\n".join(lines) + f"\n{note}")

    # /orders: today's orders, then the waiting ones with Confirm / Reject buttons.
    def cmd_orders(self, args) -> Reply:
        from marketlens.trading import monitor as mon
        orders = mon.orders_today(url=self.url, now=self.now())
        waiting = mon.waiting_signals(self.url, self.now())
        lines = [f"{o.placed_at} {o.side} {o.qty} {o.symbol}: {o.status}"
                 + (f" #{o.broker_order_id}" if o.broker_order_id else "") + (f" ({o.reason})" if o.reason else "")
                 for o in orders.head(MAX_LINES).itertuples()]
        text = f"🧾 Today's orders ({len(orders)}):\n" + ("\n".join(lines) if lines else "none")
        buttons = []
        if not waiting.empty:
            text += f"\n\n🔔 Waiting for your Confirm ({len(waiting)}):"
            for w in waiting.itertuples():
                text += f"\nSignal {w.signal_id} ({w.strategy}): {w.legs}, waiting {w.minutes_waiting:g} min"
                buttons.append([(f"✅ Confirm {w.signal_id}", f"confirm:{w.signal_id}"),
                                (f"❌ Reject {w.signal_id}", f"reject:{w.signal_id}")])
        return Reply(text, buttons)

    # /pnl: today's P&L against the daily and per-strategy limits.
    def cmd_pnl(self, args) -> Reply:
        from marketlens.trading import monitor as mon
        live = config.TRADING_MODE == "live"
        daily, per = mon.loss_limits(url=self.url, now=self.now(), live_total=mon.live_day_total() if live else None)
        lines = [f"💰 Today: {rs(daily['pnl'])} · daily loss limit {rs(daily['limit'])} ({daily['used']:.0%} used)"]
        lines += [f"  {p.strategy}: {rs(p.pnl)}" + (f" · limit {rs(p.limit)} ({p.used:.0%})" if p.limit else "")
                  for p in per.itertuples()] or ["  no strategy has traded today"]
        return Reply("\n".join(lines))

    # /strategies: every deployment.
    def cmd_strategies(self, args) -> Reply:
        from marketlens.trading import live_runner as lr
        deps = lr.list_deployments(self.url)
        if deps.empty:
            return Reply("No deployed strategies. Deploy one on page 11 (Live Algo Trading).")
        lines = [f"{_icon(d.status)} {d.name} · {d.mode.upper()} · {d.status} · lots x{d.lots_multiplier} · "
                 f"{'confirm' if d.confirm else 'auto'} · today {rs(d.pnl)}" for d in deps.itertuples()]
        return Reply("🤖 Strategies:\n" + "\n".join(lines) + "\n\n/pause NAME or /resume NAME")

    # The deployment whose name matches (whole name, or the only one that starts with it).
    def _deployment(self, args) -> tuple[int, str]:
        from marketlens.trading import live_runner as lr
        name = " ".join(args).strip().lower()
        if not name:
            raise ValueError("Give the strategy name, e.g. /pause 9:20 straddle")
        deps = lr.list_deployments(self.url)
        exact = deps[deps["name"].str.lower() == name]
        found = exact if len(exact) else deps[deps["name"].str.lower().str.startswith(name)]
        if len(found) != 1:
            names = ", ".join(deps["name"]) or "none deployed"
            raise ValueError(f"{'More than one' if len(found) else 'No'} strategy matches '{name}'. Deployed: {names}")
        return int(found.iloc[0]["strategy_id"]), found.iloc[0]["name"]

    # /pause NAME
    def cmd_pause(self, args) -> Reply:
        from marketlens.trading import live_runner as lr
        sid, name = self._deployment(args)
        lr.set_status(sid, "paused", self.url)
        return Reply(f"⏸ {name} paused: no new orders; open positions stay.")

    # /resume NAME (live: only when it is ready for live trading)
    def cmd_resume(self, args) -> Reply:
        from marketlens.trading import live_runner as lr
        sid, name = self._deployment(args)
        lr.set_status(sid, "running", self.url)
        return Reply(f"▶️ {name} running again.")

    # /alerts
    def cmd_alerts(self, args) -> Reply:
        from marketlens.alerts.price_alerts import list_alerts
        table = list_alerts(active_only=True, url=self.url)
        if table.empty:
            return Reply("No active price alerts. Add one: /addalert NIFTY above 25500")
        lines = [f"#{a.id} {a.description}" + (f" (last {a.last_value:,.2f})" if a.last_value == a.last_value
                                                 and a.last_value is not None else "") for a in table.itertuples()]
        return Reply("🔔 Active price alerts:\n" + "\n".join(lines) + "\n\n/delalert ID to delete")

    # /addalert SYMBOL above|below LEVEL
    def cmd_addalert(self, args) -> Reply:
        from marketlens.alerts.price_alerts import add_alert, describe, list_alerts
        if len(args) != 3 or args[1].lower() not in ("above", "below"):
            raise ValueError("Use: /addalert SYMBOL above|below LEVEL, e.g. /addalert NIFTY above 25500")
        try:
            level = float(args[2].replace(",", ""))
        except ValueError:
            raise ValueError(f"'{args[2]}' is not a number") from None
        alert_id = add_alert(args[0], args[1].upper(), level, note="added from Telegram", url=self.url)
        row = list_alerts(url=self.url).set_index("id").loc[alert_id]
        return Reply(f"🔔 Alert #{alert_id} added: {describe({**row.to_dict(), 'id': alert_id})}")

    # /delalert ID
    def cmd_delalert(self, args) -> Reply:
        from marketlens.alerts.price_alerts import delete_alert
        if len(args) != 1 or not args[0].lstrip("#").isdigit():
            raise ValueError("Use: /delalert ID (the number from /alerts)")
        alert_id = int(args[0].lstrip("#"))
        return Reply(f"🗑 Alert #{alert_id} deleted." if delete_alert(alert_id, self.url) else f"No alert #{alert_id}.")

    # /stop: kill switch ON.
    def cmd_stop(self, args) -> Reply:
        self.service().kill("switched on from Telegram (/stop)")
        return Reply("🛑 Kill switch ON: no new orders today. Exits (SL / target / Stop) still go through.\n"
                     "/resume_all to switch it off, /squareoff to close everything.")

    # /resume_all: asks first.
    def cmd_resume_all(self, args) -> Reply:
        stamp = int(time.time())
        return Reply("Are you sure? This switches the kill switch OFF and runs every paused strategy again.",
                     [[("✅ Yes, resume all", f"resume_all:yes:{stamp}"), ("❌ No", "resume_all:no")]])

    # /squareoff: asks to confirm.
    def cmd_squareoff(self, args) -> Reply:
        stamp = int(time.time())
        what = "Dhan's exit-all (every position)" if config.TRADING_MODE == "live" else "every paper position"
        return Reply(f"⛔ Square off everything? This closes {what}, switches the kill switch on and stops every "
                     "deployed strategy.", [[("⛔ Yes, close everything", f"squareoff:yes:{stamp}"),
                                             ("❌ No", "squareoff:no")]])

    # /report
    def cmd_report(self, args) -> Reply:
        return Reply(daily_report(self.url, self.now()))

    # /login: today's Dhan login and how to renew it.
    def cmd_login(self, args) -> Reply:
        from marketlens.broker.session import DAILY_STEPS
        from marketlens.trading import monitor as mon
        login = mon.login_today(self.url, self.now())
        text = f"🔑 Dhan login today: {_login_line(login)}"
        if login is None or login["status"] != "active":
            text += "\n\n" + DAILY_STEPS
        return Reply(text)

    # ---------------------------------------------------------------- tap buttons

    # Answer a tap on a button: confirm:ID, reject:ID, resume_all:yes:TIME, squareoff:yes:TIME, ...:no.
    def callback(self, data: str) -> Reply:
        parts = (data or "").split(":")
        action = parts[0]
        try:
            if action in ("confirm", "reject") and len(parts) == 2 and parts[1].isdigit():
                service = self.service()
                result = (service.confirm_signal if action == "confirm" else service.reject_signal)(int(parts[1]))
                icon = {"SENT": "✅", "CANCELLED": "❌"}.get(result.status, "⚠️")
                return Reply(f"{icon} Signal {parts[1]}: {result.status}. {result.message or ''}".strip())
            if action in ("resume_all", "squareoff"):
                if parts[1:2] != ["yes"]:
                    return Reply("OK, nothing was changed.")
                if len(parts) < 3 or not parts[2].isdigit() or time.time() - int(parts[2]) > CONFIRM_SECONDS:
                    return Reply(f"This button is older than {CONFIRM_SECONDS // 60} minutes. Send /{action} again.")
                return self._resume_all() if action == "resume_all" else self._square_off()
        except Exception as err:
            record_error(__name__, f"Telegram button {data!r} failed: {type(err).__name__}: {err}", url=self.url)
            return Reply(f"⚠️ That did not work: {type(err).__name__}. The details are in the error log.")
        return Reply("Unknown button.")

    # Kill switch off, and every paused deployment running again (live: only the ready ones).
    def _resume_all(self) -> Reply:
        from marketlens.trading import live_runner as lr
        self.service().unkill("switched off from Telegram (/resume_all)")
        deps = lr.list_deployments(self.url)
        lines = []
        for d in deps[deps["status"] == "paused"].itertuples():
            try:
                lr.set_status(int(d.strategy_id), "running", self.url)
                lines.append(f"▶️ {d.name} running")
            except ValueError as err:
                lines.append(f"⚠️ {d.name} still paused: {err}")
        return Reply("🟢 Kill switch OFF: orders allowed again.\n" + ("\n".join(lines) or "No paused strategies."))

    # Close everything, kill switch on, stop every deployment.
    def _square_off(self) -> Reply:
        from marketlens.trading import live_runner as lr
        stopped = lr.set_all("stopped", "Stopped: square off everything from Telegram.", self.url)
        result = self.service().kill("square off everything from Telegram (/squareoff)", square_off=True)
        return Reply(f"⛔ Done. {result or 'Nothing was open.'}\nKill switch ON; {stopped} strategy(ies) stopped.")


# ---------------------------------------------------------------- small helpers

# 🟢 running, ⏸ paused, 🟠 stopping, ⏹ stopped.
def _icon(status: str) -> str:
    return {"running": "🟢", "paused": "⏸", "stopping": "🟠", "stopped": "⏹"}.get(status, "")


# One line about today's Dhan login.
def _login_line(login: dict | None) -> str:
    if login is None:
        return "❌ not checked today (send /login for the steps)"
    if login["status"] == "active":
        until = f" until {login['expiry'].astimezone(IST):%d-%b %H:%M}" if login.get("expiry") else ""
        return f"✅ valid{until}"
    return f"❌ {login['status']}: {login['message'] or ''}".strip()


# Today's report: P&L by strategy, orders, kill switch and alerts triggered today.
def daily_report(url: str | None = None, now: dt.datetime | None = None) -> str:
    from marketlens.alerts.price_alerts import list_alerts
    from marketlens.trading import monitor as mon
    now = now or dt.datetime.now(IST)
    daily, per = mon.loss_limits(url=url, now=now)
    orders = mon.orders_today(url=url, now=now)
    killed, why = mon.kill_state(url=url, now=now)
    alerts = list_alerts(url=url)
    today = [a for a in alerts.itertuples()
             if a.triggered_at is not None and a.triggered_at == a.triggered_at
             and a.triggered_at.astimezone(IST).date() == now.date()]
    counts = orders["status"].value_counts().to_dict() if len(orders) else {}
    lines = [f"📊 MarketLens report {now:%d-%b-%Y} ({config.TRADING_MODE.upper()})",
             f"P&L today: {rs(daily['pnl'])} (daily limit {rs(daily['limit'])}, {daily['used']:.0%} used)"]
    lines += [f"  {p.strategy}: {rs(p.pnl)}" for p in per.itertuples()]
    lines.append(f"Orders: {len(orders)}" + (" (" + ", ".join(f"{n} {s.lower()}" for s, n in counts.items()) + ")"
                                             if counts else ""))
    lines.append(f"Kill switch: {'ON (' + why + ')' if killed else 'off'}")
    lines.append(f"Price alerts triggered today: {len(today)}" + "".join(f"\n  #{a.id} {a.description}" for a in today))
    return "\n".join(lines)


# ---------------------------------------------------------------- python-telegram-bot

# Telegram buttons from Reply.buttons (None when there are none).
def _markup(buttons):
    if not buttons:
        return None
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row]
                                 for row in buttons])


# Keep a copy of the bot's answer in notifications (source "bot").
def _save_reply(core: BotCore, text: str):
    from marketlens.alerts.telegram import save_notification
    save_notification(text, "sent", None, "bot", "reply", core.url)


# A command from Telegram: only your chat is answered; the work runs in a thread (database calls block).
async def on_command(update, context):
    core: BotCore = context.application.bot_data["core"]
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return
    user = update.effective_user.username if update.effective_user else None
    if not core.allowed(chat.id):
        await asyncio.to_thread(core.refused, chat.id, user, message.text)
        return
    command = (message.text or "").split()[0] if message.text else "/help"
    reply = await asyncio.to_thread(core.handle, command, list(context.args or []))
    await message.reply_text(reply.text[:4000], reply_markup=_markup(reply.buttons))
    await asyncio.to_thread(_save_reply, core, reply.text)


# A tap on a button: only from your chat; the message is replaced by the answer.
async def on_button(update, context):
    core: BotCore = context.application.bot_data["core"]
    query = update.callback_query
    chat = update.effective_chat
    if query is None:
        return
    if chat is None or not core.allowed(chat.id):
        user = update.effective_user.username if update.effective_user else None
        await asyncio.to_thread(core.refused, chat.id if chat else "?", user, f"button {query.data}")
        await query.answer()
        return
    await query.answer()
    reply = await asyncio.to_thread(core.callback, query.data)
    await query.edit_message_text(reply.text[:4000], reply_markup=_markup(reply.buttons))
    await asyncio.to_thread(_save_reply, core, reply.text)


# Any other message: from your chat a short hint, from others nothing (logged).
async def on_other(update, context):
    core: BotCore = context.application.bot_data["core"]
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return
    if not core.allowed(chat.id):
        user = update.effective_user.username if update.effective_user else None
        await asyncio.to_thread(core.refused, chat.id, user, message.text)
        return
    await message.reply_text("I only understand commands. Send /help for the list.")


# Every minute: a heartbeat; at 15:45 on weekdays: the daily report (when switched on on page 12).
async def background_loop(core: BotCore, sleep=asyncio.sleep):
    from marketlens.alerts.telegram import notify
    from marketlens.trading.live_runner import beat
    sent_on = None
    while True:
        now = core.now()
        try:
            await asyncio.to_thread(beat, HEARTBEAT, "answering commands", core.url)
            if now.weekday() < 5 and now.time() >= REPORT_AT and sent_on != now.date():
                text = await asyncio.to_thread(daily_report, core.url, now)
                await asyncio.to_thread(notify, text, None, "bot", "daily_report", core.url)
                sent_on = now.date()
        except Exception as err:                       # the database may be down: try again next minute
            log.warning("Telegram bot background step failed: %s", err)
        await sleep(60)


# Build the bot (commands, buttons, the command menu in Telegram, the background loop).
def build_application(token: str, core: BotCore | None = None):
    from telegram import BotCommand
    from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters
    core = core or BotCore()

    # When the bot starts: the command menu, and the heartbeat / daily report loop.
    async def started(app):
        await app.bot.set_my_commands([BotCommand(name, text[:256]) for name, text in COMMANDS.items()])
        app.bot_data["loop"] = asyncio.create_task(background_loop(core))

    app = ApplicationBuilder().token(token).post_init(started).build()
    app.bot_data["core"] = core
    app.add_handler(CommandHandler(list(COMMANDS), on_command))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.ALL, on_other))
    return app


# python -m marketlens.alerts.bot
def main(argv: list[str] | None = None) -> int:
    if not config.is_set(config.TELEGRAM_BOT_TOKEN):
        print("TELEGRAM_BOT_TOKEN is not set in .env, so the bot cannot start.\n"
              "Make a bot with @BotFather, put its token in .env as TELEGRAM_BOT_TOKEN and your chat id as "
              "TELEGRAM_CHAT_ID (see the README: 'Create your bot with @BotFather and find your chat ID').")
        return 1
    if not config.is_set(config.TELEGRAM_CHAT_ID):
        print("TELEGRAM_CHAT_ID is not set in .env, so the bot would not answer anyone.\n"
              "Send any message to your bot, then open https://api.telegram.org/bot<TOKEN>/getUpdates and copy "
              "\"chat\":{\"id\": ...} into .env as TELEGRAM_CHAT_ID (see the README).")
        return 1
    print(f"MarketLens Telegram bot started ({config.TRADING_MODE.upper()} mode). It answers chat "
          f"{config.TELEGRAM_CHAT_ID} only. Press Ctrl+C to stop.", flush=True)
    build_application(config.TELEGRAM_BOT_TOKEN).run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
