"""Which day options expired on, for any date in the last 5 years.

Dhan's expired-options data is relative (ATM, ATM+1 ...) and does not say the
expiry date, so we work it out from the exchange rules below. NSE/BSE changed
the expiry weekday several times; each rule lists the date it started.
If the rule's expiry day was a market holiday, the expiry moved to the trading
day before it (adjust_for_holidays does that, using the days that have data).

Sources: NSE/BSE circulars; SEBI circular of 1-Oct-2024 (one weekly index per
exchange from 20-Nov-2024); NSE/BSE expiry swap from 1-Sep-2025 (NSE Tuesday,
BSE Thursday). Rules marked "check" are from memory; confirm in old circulars.
"""
import datetime as dt

MON, TUE, WED, THU, FRI = 0, 1, 2, 3, 4
WEEK, MONTH = "WEEK", "MONTH"

# (underlying, WEEK/MONTH) -> list of (rule starts on, expiry weekday); None = no such expiry
RULES = {
    ("NIFTY", WEEK): [(dt.date(2000, 1, 1), THU), (dt.date(2025, 9, 1), TUE)],
    ("NIFTY", MONTH): [(dt.date(2000, 1, 1), THU), (dt.date(2025, 9, 1), TUE)],
    # SENSEX weeklies relaunched May 2023 on Friday (check), Tuesday from Jan 2025 (check),
    # Thursday from 1-Sep-2025 (confirmed)
    ("SENSEX", WEEK): [(dt.date(2000, 1, 1), None), (dt.date(2023, 5, 15), FRI),
                       (dt.date(2025, 1, 1), TUE), (dt.date(2025, 9, 1), THU)],
    ("SENSEX", MONTH): [(dt.date(2000, 1, 1), FRI), (dt.date(2025, 1, 1), TUE),
                        (dt.date(2025, 9, 1), THU)],
    # BANKNIFTY weekly: Thursday, Wednesday from Sep 2023 (check), none from 20-Nov-2024 (confirmed)
    ("BANKNIFTY", WEEK): [(dt.date(2000, 1, 1), THU), (dt.date(2023, 9, 4), WED),
                          (dt.date(2024, 11, 20), None)],
    # BANKNIFTY monthly: Thursday; Wednesday Mar-Dec 2024 (check); Thursday 2025; Tuesday from Sep 2025
    ("BANKNIFTY", MONTH): [(dt.date(2000, 1, 1), THU), (dt.date(2024, 3, 1), WED),
                           (dt.date(2025, 1, 1), THU), (dt.date(2025, 9, 1), TUE)],
    # FINNIFTY weekly on Tuesday until 20-Nov-2024; monthly Tuesday, Thursday in 2025 (check)
    ("FINNIFTY", WEEK): [(dt.date(2000, 1, 1), TUE), (dt.date(2024, 11, 20), None)],
    ("FINNIFTY", MONTH): [(dt.date(2000, 1, 1), TUE), (dt.date(2025, 1, 1), THU),
                          (dt.date(2025, 9, 1), TUE)],
}
# Stock options: monthly only, last Thursday, last Tuesday from Sep 2025
STOCK_MONTH = [(dt.date(2000, 1, 1), THU), (dt.date(2025, 9, 1), TUE)]


# The expiry weekday in force on `day` (None when that kind of expiry did not exist).
def weekday_rule(underlying: str, day: dt.date, flag: str) -> int | None:
    flag = flag.upper()
    rules = RULES.get((underlying.upper(), flag))
    if rules is None:
        if flag == WEEK:
            return None
        rules = STOCK_MONTH
    weekday = None
    for start, wd in rules:
        if day >= start:
            weekday = wd
    return weekday


# True when weekly options existed for this underlying on that day.
def has_weekly(underlying: str, day: dt.date) -> bool:
    return weekday_rule(underlying, day, WEEK) is not None


# Last given weekday of a month.
def last_weekday(year: int, month: int, weekday: int) -> dt.date:
    nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
    day = nxt - dt.timedelta(days=1)
    return day - dt.timedelta(days=(day.weekday() - weekday) % 7)


# The nearest expiry on or after `day` by the rules (before any holiday shift).
def rule_expiry(underlying: str, day: dt.date, flag: str) -> dt.date:
    """flag WEEK: the next expiry weekday; MONTH: the last expiry weekday of the month."""
    flag = flag.upper()
    weekday = weekday_rule(underlying, day, flag)
    if weekday is None:
        raise ValueError(f"{underlying} had no weekly options on {day:%d-%b-%Y}; use monthly.")
    if flag == WEEK:
        expiry = day + dt.timedelta(days=(weekday - day.weekday()) % 7)
        # the rule may change between day and that expiry (e.g. a Thursday rule ending on Monday)
        later = weekday_rule(underlying, expiry, flag)
        if later is not None and later != weekday:
            expiry = day + dt.timedelta(days=(later - day.weekday()) % 7)
        return expiry
    expiry = last_weekday(day.year, day.month, weekday)
    if expiry < day:
        year, month = day.year + (day.month == 12), day.month % 12 + 1
        expiry = last_weekday(year, month, weekday_rule(underlying, dt.date(year, month, 1), flag))
    return expiry


# Move an expiry that fell on a holiday to the trading day before it.
def adjust_for_holidays(expiry: dt.date, trading_days: set) -> dt.date:
    """trading_days: dates known to have market data. If the expiry lies inside that known
    period but has no data, it was a holiday, so the expiry was the trading day before."""
    if not trading_days or expiry in trading_days or expiry > max(trading_days) \
            or expiry < min(trading_days):
        return expiry
    before = [d for d in trading_days if d < expiry]
    return max(before) if before else expiry


# The expiry that an option traded on `day` belonged to (rules + holiday shift).
def expiry_for(underlying: str, day: dt.date, flag: str, trading_days: set | None = None) -> dt.date:
    return adjust_for_holidays(rule_expiry(underlying, day, flag), trading_days or set())
