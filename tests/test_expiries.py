"""Tests for marketlens/options/expiries.py (which day options expired on)."""
import datetime as dt

import pytest

from marketlens.options.expiries import (MONTH, WEEK, adjust_for_holidays, expiry_for, has_weekly,
                                         rule_expiry)


# NIFTY weekly: Thursday until Aug 2025, Tuesday from Sep 2025 (confirmed dates).
@pytest.mark.parametrize("day, expected", [
    (dt.date(2025, 8, 25), dt.date(2025, 8, 28)),    # last Thursday weekly
    (dt.date(2025, 8, 29), dt.date(2025, 9, 2)),     # rule changes: first Tuesday weekly
    (dt.date(2026, 10, 28), dt.date(2026, 11, 3)),
    (dt.date(2026, 11, 3), dt.date(2026, 11, 3)),    # expiry day itself
])
def test_nifty_weekly(day, expected):
    assert rule_expiry("NIFTY", day, WEEK) == expected


# Monthly expiries: last Thursday before Sep 2025, last Tuesday after.
def test_monthly():
    assert rule_expiry("NIFTY", dt.date(2024, 6, 3), MONTH) == dt.date(2024, 6, 27)
    assert rule_expiry("NIFTY", dt.date(2026, 10, 28), MONTH) == dt.date(2026, 11, 24)
    assert rule_expiry("RELIANCE", dt.date(2026, 10, 1), MONTH) == dt.date(2026, 10, 27)


# BANKNIFTY weeklies stopped on 20-Nov-2024; SENSEX weekly is Thursday now.
def test_weekly_availability():
    assert has_weekly("BANKNIFTY", dt.date(2024, 11, 13))
    assert not has_weekly("BANKNIFTY", dt.date(2024, 11, 20))
    with pytest.raises(ValueError, match="monthly"):
        rule_expiry("BANKNIFTY", dt.date(2025, 3, 3), WEEK)
    assert rule_expiry("SENSEX", dt.date(2026, 10, 27), WEEK) == dt.date(2026, 10, 29)


# A holiday expiry moves to the trading day before it; unknown periods are left alone.
def test_holiday_shift():
    week = {dt.date(2026, 11, d) for d in (2, 3, 4, 5, 6, 9, 11, 12)}   # 10-Nov missing = holiday
    assert adjust_for_holidays(dt.date(2026, 11, 10), week) == dt.date(2026, 11, 9)
    assert adjust_for_holidays(dt.date(2026, 11, 3), week) == dt.date(2026, 11, 3)
    assert adjust_for_holidays(dt.date(2026, 11, 17), week) == dt.date(2026, 11, 17)  # after known days
    assert expiry_for("NIFTY", dt.date(2026, 11, 5), WEEK, week) == dt.date(2026, 11, 9)
