"""Is the NSE market open right now, and when does it open next? (No internet, no database.)

    market_clock(now)  -> {"state": "open" | "pre_open" | "closed" | "holiday", "label": "Open", "now": ...,
                           "next_open": ..., "closes_at": ..., "reason": ..., "list_ends": ..., "warning": ...}

Times are India time (IST). Sessions: pre-open 09:00-09:15, normal trading 09:15-15:30, Monday to Friday,
except the NSE holidays in data/nse_holidays.csv. That file covers one year at a time; past its last row only
weekends are known, so the answer then carries a warning to add the next year's list.
"""
import datetime as dt

from marketlens.broker.candles import IST, MARKET_CLOSE, MARKET_OPEN
from marketlens.scheduler.jobs import HOLIDAYS_FILE, load_holidays

PRE_OPEN = dt.time(9, 0)
LABELS = {"open": "Open", "pre_open": "Pre-open", "closed": "Closed", "holiday": "Holiday"}
# Where NSE publishes each year's trading holidays (a circular every December, and this page)
NSE_HOLIDAYS_PAGE = "https://www.nseindia.com/resources/exchange-communication-holidays"
LOOK_AHEAD_DAYS = 30                  # no market ever stays shut longer than this


# The last date in the holiday list (None when the list is empty).
def list_end(holidays: dict) -> dt.date | None:
    return max(holidays) if holidays else None


# The warning to show when `day` is past the end of the holiday list, else None.
def list_warning(day: dt.date, holidays: dict) -> str | None:
    end = list_end(holidays)
    if end is None:
        return (f"No NSE holiday list found: add it to data/nse_holidays.csv (NSE publishes it at "
                f"{NSE_HOLIDAYS_PAGE}). Only weekends are known.")
    if day > end:
        return (f"Holiday list ends {end:%d-%b-%Y}, update data/nse_holidays.csv (NSE publishes next year's list "
                f"each December at {NSE_HOLIDAYS_PAGE}). Only weekends are known after that date.")
    return None


# Why `day` has no trading: ("holiday", "NSE holiday: Diwali"), ("closed", "Saturday"), or None on a trading day.
def day_closed(day: dt.date, holidays: dict) -> tuple[str, str] | None:
    if day.weekday() >= 5:
        return "closed", day.strftime("%A")
    if day in holidays:
        return "holiday", f"NSE holiday: {holidays[day]}"
    return None


# The next trading day on or after `day` (None if none within LOOK_AHEAD_DAYS).
def next_trading_day(day: dt.date, holidays: dict) -> dt.date | None:
    for i in range(LOOK_AHEAD_DAYS):
        candidate = day + dt.timedelta(days=i)
        if day_closed(candidate, holidays) is None:
            return candidate
    return None


# The market's state at `now`, with the next opening time. holidays: {date: name}; None = read the CSV file.
def market_clock(now: dt.datetime | None = None, holidays: dict | None = None) -> dict:
    holidays = load_holidays(HOLIDAYS_FILE) if holidays is None else holidays
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    today, time_now = now.date(), now.time()
    closed = day_closed(today, holidays)
    if closed:
        state, reason = closed
        open_day = next_trading_day(today + dt.timedelta(days=1), holidays)
    elif time_now < PRE_OPEN:
        state, reason, open_day = "closed", "Before the pre-open session (09:00)", today
    elif time_now < MARKET_OPEN:
        state, reason, open_day = "pre_open", "Pre-open session (09:00-09:15): orders are collected, no trades yet", today
    elif time_now <= MARKET_CLOSE:
        state, reason = "open", "Normal trading (09:15-15:30)"
        open_day = next_trading_day(today + dt.timedelta(days=1), holidays)
    else:
        state, reason = "closed", "After the close (15:30)"
        open_day = next_trading_day(today + dt.timedelta(days=1), holidays)
    next_open = dt.datetime.combine(open_day, MARKET_OPEN, IST) if open_day else None
    end = list_end(holidays)
    warning = list_warning(today, holidays)
    if warning is None and next_open is not None and end is not None and next_open.date() > end:
        warning = list_warning(next_open.date(), holidays)      # today is listed, the next opening is not
    return {"state": state, "label": LABELS[state], "reason": reason, "now": now.isoformat(timespec="seconds"),
            "next_open": next_open.isoformat() if next_open else None,
            "closes_at": dt.datetime.combine(today, MARKET_CLOSE, IST).isoformat() if state == "open" else None,
            "list_ends": end.isoformat() if end else None, "warning": warning}


# The next `count` holidays from `day` on: [(date, name), ...] (the Settings page's "next: ..." note).
def upcoming_holidays(day: dt.date, holidays: dict, count: int = 3) -> list[tuple[dt.date, str]]:
    return [(d, holidays[d]) for d in sorted(d for d in holidays if d >= day)[:count]]


# The Settings page's note about holidays, e.g. "Market jobs skip weekends and the 16 NSE holidays ... (next: ...)".
def holiday_note(day: dt.date, holidays: dict) -> str:
    coming = upcoming_holidays(day, holidays)
    text = f"Market jobs skip weekends and the {len(holidays)} NSE holidays in data/nse_holidays.csv"
    if coming:
        text += " (next: " + ", ".join(f"{d:%d-%b} {name}" for d, name in coming) + ")"
    return text + ". Add next year's list from NSE's holiday circular each December."
