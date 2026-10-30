"""Tests for marketlens/trading/forward.py (module 6 forward testing).

1. Live = backtest: running a day minute by minute with run_day_until gives exactly the backtest's trades,
   and an event never changes once it has appeared (so orders are never sent twice or taken back).
2. ForwardRunner on DEMO prices that move every minute, with a fake clock, saving to TEST_DATABASE_URL.
3. Indicator strategies, the stop request, a restart, and the command line (--once --at).
"""
import datetime as dt

import pandas as pd
import pytest

from marketlens.backtest import options_bt as ob
from marketlens.broker.candles import IST
from marketlens.broker.demo_data import DemoDataClient
from marketlens.trading import forward as fw
from test_options_bt_rules import CE, DAY, EXPIRY, PE, day_frame, settings

FWD_DAY = dt.date(2026, 10, 28)          # a Wednesday; NIFTY weekly expiry Tuesday 3-Nov-2026


# Every entry / exit event of a (partial) day run, keyed like the runner does.
def events_of(run) -> dict:
    out = {}
    for t in run.trades:
        out[f"E|{t.leg_no}|{t.entry_time:%H:%M}"] = (t.symbol, t.side, t.entry_price)
        out[f"X|{t.leg_no}|{t.entry_time:%H:%M}"] = (t.symbol, t.exit_reason, t.exit_time, t.exit_price)
    for leg in run.legs:
        if leg.status == "open":
            out[f"E|{leg.no}|{leg.entry_at:%H:%M}"] = (f"NIFTY {leg.strike:g} {leg.spec.option_type} {EXPIRY:%d-%b-%Y}",
                                                       leg.spec.side, leg.entry)
    return out


# ---- 1. live = backtest ----

@pytest.mark.parametrize("rules", [
    dict(legs=[ob.LegSpec("SELL", "CE", unit="points", sl=10, reentry="reexecute", max_reentries=2),
               ob.LegSpec("SELL", "PE", unit="points", sl=50)], move_sl_to_cost=True),
    dict(legs=[ob.LegSpec("SELL", "CE"), ob.LegSpec("SELL", "PE")], m2m_sl=300),
    dict(legs=[ob.LegSpec("BUY", "CE")], entry_mode="breakout", range_end=dt.time(9, 18), range_source="leg",
         breakout_side="high"),
])
def test_live_minute_by_minute_equals_backtest(rules):
    series = {CE: [(100, 102, 98, 101), (101, 104, 97, 103), (103, 103, 99, 100), (100, 104, 100, 101),
                   (102, 115, 101, 112), (112, 118, 110, 116), 110, 120, 125],
              PE: [100, 100, 95, 95, 98, 97, 96, 99, 101], ("CE", 26100.0): [60, 61, 62, 63, 64, 70, 72, 75, 80]}
    s = settings(**rules)
    arrays = ob.DayArrays.from_frame(day_frame(series), DAY, EXPIRY)
    seen = {}
    for now in range(1, ob.minute_index(s.exit_time) + 2):
        current = events_of(ob.run_day_until(s, arrays, now))
        for key, value in seen.items():
            assert current.get(key) == value, f"event {key} changed at minute {now}"
        seen.update(current)
    trades, _, _ = ob.run_day(s, arrays)
    full = events_of(type("R", (), {"trades": trades, "legs": []})())
    assert seen == full and len(trades) >= 1


# ---- 2. live minute candles ----

def test_live_minutes_and_csv(tmp_path):
    chain = DemoDataClient(spots={"NIFTY": 25000}, vix=14, now=dt.datetime(2026, 10, 28, 9, 20, 3, tzinfo=IST),
                           moving=True).get_option_chain("NIFTY", dt.date(2026, 11, 3))
    path = tmp_path / "day.csv"
    lm = fw.LiveMinutes(FWD_DAY, dt.date(2026, 11, 3), path)
    assert fw.LiveMinutes.candle_of(dt.datetime(2026, 10, 28, 9, 20, 3, tzinfo=IST)) == 4     # the 09:19 candle
    lm.add_chain(chain, dt.datetime(2026, 10, 28, 9, 20, 3, tzinfo=IST))
    frame = lm.frame()
    assert set(frame["ts"].dt.time) == {dt.time(9, 19)} and len(frame) == 2 * len(chain.table)
    again = fw.LiveMinutes(FWD_DAY, dt.date(2026, 11, 3), path)                 # a restarted process
    pd.testing.assert_frame_equal(again.frame(), frame)


# The DEMO chain moves with the minute when moving=True (and stays fixed without it).
def test_demo_chain_moves():
    def spot_at(minute, moving=True):
        when = dt.datetime(2026, 10, 28, 9, minute, tzinfo=IST)
        return DemoDataClient(spots={"NIFTY": 25000}, vix=14, now=when, moving=moving).get_option_chain(
            "NIFTY", dt.date(2026, 11, 3)).spot
    assert spot_at(20) != spot_at(40)
    assert spot_at(20, moving=False) == spot_at(40, moving=False) == 25000


# ---- 3. the runner on DEMO data, with a fake clock (test database) ----

class FakeClock:
    """Moves a DEMO client's clock and the runner's clock together."""

    def __init__(self, client, start: dt.datetime):
        self.client, self.now = client, start
        client._now = start

    def __call__(self):
        return self.now

    def tick(self, minutes: int = 1):
        self.now += dt.timedelta(minutes=minutes)
        self.client._now = self.now


@pytest.fixture
def straddle(db_url, db_session):
    s = ob.template_settings("920_straddle", "NIFTY", FWD_DAY, FWD_DAY, exit_time=dt.time(10, 0))
    return ob.save_backtest_strategy("Fwd straddle", s, url=db_url), s


# Run cycles from `start` for `minutes` minutes; returns the runner and the states seen.
def run_minutes(strategy_id, db_url, tmp_path, start=dt.time(9, 16, 3), minutes=46, runner=None, clock=None):
    if runner is None:
        client = DemoDataClient(spots={"NIFTY": 25000}, vix=14, moving=True)
        clock = FakeClock(client, dt.datetime.combine(FWD_DAY, start, IST))
        runner = fw.ForwardRunner(strategy_id, 500_000, client=client, url=db_url, clock=clock, data_dir=tmp_path)
    states = []
    for _ in range(minutes):
        states.append(runner.cycle()["state"])
        clock.tick()
    return runner, clock, states


def test_forward_day_matches_backtest(straddle, db_url, tmp_path):
    sid, s = straddle
    runner, clock, states = run_minutes(sid, db_url, tmp_path)               # 09:16 .. 10:01
    assert states[0] == "waiting" and "in_trade" in states and states[-1] == "done"
    status = fw.status(sid, url=db_url)
    trades = status["params"]["trades"]
    assert len(trades) >= 2 and {t["exit_reason"] for t in trades} <= {"SL", "TIME"}
    # Entries fill at the live price the rule used (no slippage set): paper = rule
    first = [t for t in trades if t["entry_time"] == "09:20"]
    assert len(first) == 2 and all(t["paper_entry"] == t["rule_entry"] for t in first)
    # The backtest of the recorded minutes gives exactly the same trades
    arrays = ob.DayArrays.from_frame(runner.minutes.frame(), FWD_DAY, runner.minutes.expiry)
    bt, _, _ = ob.run_day(s, arrays)
    assert [(t.symbol, t.entry_time.strftime("%H:%M"), t.exit_reason, t.exit_price) for t in bt] == \
           [(t["symbol"], t["entry_time"], t["exit_reason"], t["rule_exit"]) for t in trades]
    # Every rule event became exactly one filled paper order; everything is closed at the end
    orders = runner.broker.get_orders()
    assert len(orders) == 2 * len(trades) and set(orders["status"]) == {"FILLED"}
    assert not runner.broker.positions
    assert status["pnl"] == pytest.approx(sum(t["paper_pnl"] for t in trades) - orders["costs"].sum(), abs=0.05)
    hist = fw.history(sid, url=db_url)
    assert list(hist["date"]) == [FWD_DAY] and hist["forward_pnl"].iloc[0] == status["pnl"]


# A restarted process (new runner, same CSV and database) continues without repeating orders.
def test_restart_does_not_repeat_orders(straddle, db_url, tmp_path):
    sid, _ = straddle
    runner, clock, _ = run_minutes(sid, db_url, tmp_path, minutes=8)          # 09:16 .. 09:23: in trade
    before = len(runner.broker.get_orders())
    again = fw.ForwardRunner(sid, 500_000, client=runner.client, url=db_url, clock=clock, data_dir=tmp_path)
    assert again.broker.open_count() == 2
    again.cycle()
    assert len(again.broker.get_orders()) == before


# Stop from the page: the next cycle closes everything and ends.
def test_stop_request(straddle, db_url, tmp_path):
    sid, _ = straddle
    runner, clock, _ = run_minutes(sid, db_url, tmp_path, minutes=6)          # entered at 09:20
    assert runner.broker.open_count() == 2
    assert fw.request_stop(sid, url=db_url)
    result = runner.cycle()
    assert result["stop"] and runner.broker.open_count() == 0
    status = fw.status(sid, url=db_url)
    assert (status["status"], status["state"]) == ("stopped", "stopped") and not status["alive"]


# Started after the entry time: no made-up entry, it waits for the next day.
def test_started_late(straddle, db_url, tmp_path):
    sid, _ = straddle
    _, _, states = run_minutes(sid, db_url, tmp_path, start=dt.time(9, 40, 3), minutes=2)
    assert states == ["done", "done"]
    assert "next market day" in fw.status(sid, url=db_url)["message"]


# Weekends and nights: market closed.
def test_market_closed(straddle, db_url, tmp_path):
    sid, _ = straddle
    client = DemoDataClient(spots={"NIFTY": 25000}, vix=14, moving=True)
    clock = FakeClock(client, dt.datetime(2026, 10, 31, 11, 0, tzinfo=IST))            # Saturday
    runner = fw.ForwardRunner(sid, 500_000, client=client, url=db_url, clock=clock, data_dir=tmp_path)
    assert runner.cycle()["state"] == "closed"


# The command line: one DEMO cycle at a chosen time.
def test_command_line_once(straddle, db_url, tmp_path, monkeypatch):
    sid, _ = straddle
    monkeypatch.setattr(fw, "DATA_DIR", tmp_path)
    assert fw.main(["--strategy", str(sid), "--once", "--at", "2026-10-28 09:18"], url=db_url) == 0
    status = fw.status(sid, url=db_url)
    assert status["state"] == "waiting" and status["params"]["market_time"] == "09:18"
    with pytest.raises(SystemExit):
        fw.main(["--once"], url=db_url)                                          # --strategy is required


# ---- indicator strategies ----

def test_indicator_signals():
    s = fw.IndicatorStrategy("SBIN", fast=2, slow=3)
    falling = pd.Series([10.0, 9, 8, 7, 6, 5])
    assert s.signal(falling) is None
    assert s.signal(pd.Series([10.0, 9, 8, 7, 6, 5, 9])) == "BUY"                # fast EMA jumps above slow
    assert s.signal(pd.Series([5.0, 6, 7, 8, 9, 10, 6])) == "SELL"
    r = fw.IndicatorStrategy("SBIN", rule="rsi", rsi_length=3)
    assert r.signal(pd.Series([10.0, 9, 8, 7, 6, 5, 4, 7])) == "BUY"
    assert fw.IndicatorStrategy.from_params(s.to_params()) == s
    with pytest.raises(ValueError):
        fw.IndicatorStrategy("SBIN", fast=5, slow=3)


class CandleClient:
    """Fake data: the whole day's 1-minute candles (including the future - the runner must ignore it)."""

    def __init__(self, closes):
        self.closes, self.calls = closes, 0

    def get_intraday(self, symbol, start, end, interval):
        ts = [dt.datetime.combine(start, dt.time(9, 15), IST) + dt.timedelta(minutes=i) for i in range(len(self.closes))]
        return pd.DataFrame({"timestamp": ts, "close": self.closes})


def test_indicator_runner(db_url, db_session, tmp_path):
    strategy = fw.IndicatorStrategy("SBIN", qty=10, fast=2, slow=3, timeframe=1, start_time=dt.time(9, 16),
                                    exit_time=dt.time(9, 30))
    sid = fw.save_indicator_strategy("SBIN ema", strategy, url=db_url)
    closes = [10.0, 9, 8, 7, 6, 5, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    now = {"t": dt.datetime.combine(FWD_DAY, dt.time(9, 21, 3), IST)}
    runner = fw.ForwardRunner(sid, 100_000, client=CandleClient(closes), url=db_url, clock=lambda: now["t"],
                              data_dir=tmp_path)
    # 09:21:03: candles 09:15-09:20 have finished (10 .. 5, falling): no signal. The 09:21 candle (9)
    # is in the data but has not finished, so it must not be used yet.
    assert runner.cycle()["state"] == "waiting" and not runner.broker.positions
    # 09:22:03: the 09:21 candle closed at 9 -> fast EMA crosses above slow -> buy 10 at 9
    now["t"] = dt.datetime.combine(FWD_DAY, dt.time(9, 22, 3), IST)
    assert runner.cycle()["state"] == "in_trade"
    pos = runner.broker.positions[("SBIN", "INTRADAY")]
    assert pos.qty == 10 and pos.avg_price == 9
    now["t"] = dt.datetime.combine(FWD_DAY, dt.time(9, 31, 3), IST)
    assert runner.cycle()["state"] == "done" and runner.broker.open_count() == 0
    trades = fw.status(sid, url=db_url)["params"]["trades"]
    assert [t["exit_reason"] for t in trades] == ["SIGNAL", "TIME"]
