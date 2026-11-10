"""Tests for marketlens/scheduler (jobs, NSE holidays, run records, the command line) and the Settings page's
job list. Every job's real work (downloads, Dhan, Telegram, pg_dump) is replaced by a fake; runs are saved
in TEST_DATABASE_URL."""
import datetime as dt
import subprocess
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from marketlens import config
from marketlens.broker.candles import IST
from marketlens.scheduler import __main__ as cli
from marketlens.scheduler import jobs

HOLIDAYS = jobs.load_holidays()
PAGE = str(Path(__file__).resolve().parent.parent / "dashboards" / "settings.py")


# A time in India.
def at(y, m, d, hh=10, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=IST)


# ---------------------------------------------------------------- holidays and trading days

# The holiday file is read; weekends and holidays are not trading days.
def test_holidays():
    assert len(HOLIDAYS) >= 15 and HOLIDAYS[dt.date(2026, 11, 10)] == "Diwali-Balipratipada"
    assert jobs.closed_reason(dt.date(2026, 11, 10), HOLIDAYS) == "NSE holiday: Diwali-Balipratipada"
    assert jobs.closed_reason(dt.date(2026, 11, 14), HOLIDAYS) == "Saturday"
    assert jobs.is_trading_day(dt.date(2026, 11, 11), HOLIDAYS)
    assert jobs.load_holidays(Path("no/such/file.csv")) == {}


# The first trading day of a month skips weekends and holidays (1-May-2026 is Maharashtra Day).
@pytest.mark.parametrize("day, first", [
    (dt.date(2026, 5, 1), False), (dt.date(2026, 5, 4), True), (dt.date(2026, 5, 5), False),
    (dt.date(2026, 11, 2), True), (dt.date(2026, 12, 1), True), (dt.date(2026, 8, 3), True),
])
def test_first_trading_day(day, first):
    assert jobs.is_first_trading_day(day, HOLIDAYS) is first


# Next runs take weekends, holidays, market hours and the first-trading-day rule into account.
def test_next_run():
    nxt = lambda job_id, now: jobs.next_run(jobs.BY_ID[job_id], now, HOLIDAYS)  # noqa: E731
    assert nxt("reference_data", at(2026, 11, 9, 7)) == at(2026, 11, 9, 8, 30)
    assert nxt("reference_data", at(2026, 11, 9, 9)) == at(2026, 11, 11, 8, 30)          # 10-Nov is a holiday
    assert nxt("rebalance_signals", at(2026, 11, 3)) == at(2026, 12, 1, 9, 20)
    assert nxt("price_alerts", at(2026, 11, 11, 15, 31)) == at(2026, 11, 12, 9, 15)
    assert nxt("chain_snapshots", at(2026, 11, 11, 9, 0)) == at(2026, 11, 11, 9, 15)
    assert nxt("backup", at(2026, 11, 10)) == at(2026, 11, 15, 10, 0)                   # Sundays too
    assert nxt("eod_report", at(2026, 11, 11, 16)) == at(2026, 11, 12, 15, 50)


# ---------------------------------------------------------------- running and recording

# Replace one job's work with `func`.
@pytest.fixture
def swap(monkeypatch):
    def do(job_id, func):
        monkeypatch.setattr(jobs.BY_ID[job_id], "func", func)
    return do


# The runs saved for one job, oldest first.
def runs(url, job_id):
    from sqlalchemy import select

    from marketlens.db import get_session
    from marketlens.models import SchedulerRun
    with get_session(url) as session:
        return [(r.status, r.message) for r in session.scalars(select(SchedulerRun).where(SchedulerRun.job_id == job_id)
                                                               .order_by(SchedulerRun.id))]


# ok, failed (also in error_log), Skip, holiday and the market-hours rule.
def test_run_job(db_url, db_session, swap):
    from marketlens.trading import monitor as mon
    swap("sync_prices", lambda now, url: "stocks: 200 rows")
    assert jobs.run_job("sync_prices", at(2026, 11, 11, 9), db_url, holidays=HOLIDAYS)[0] == "ok"
    assert jobs.run_job("sync_prices", at(2026, 11, 10, 9), db_url, holidays=HOLIDAYS) == \
        ("skipped", "NSE holiday: Diwali-Balipratipada")
    assert jobs.run_job("sync_prices", at(2026, 11, 10, 9), db_url, force=True, holidays=HOLIDAYS)[0] == "ok"

    def broken(now, url):
        raise ConnectionError("yfinance down")
    swap("sync_prices", broken)
    assert jobs.run_job("sync_prices", at(2026, 11, 11, 9), db_url, holidays=HOLIDAYS)[1].startswith(
        "ConnectionError: yfinance down")
    saved = runs(db_url, "sync_prices")
    assert [s for s, _ in saved] == ["ok", "skipped", "ok", "failed"] and saved[0][1].startswith("stocks: 200 rows (")
    assert "Scheduled job 'Sync daily prices" in mon.error_log(url=db_url, days=3650)["message"].iloc[0]

    def nothing(now, url):
        raise jobs.Skip("Dhan keys not set")
    swap("minute_data", nothing)
    assert jobs.run_job("minute_data", at(2026, 11, 11, 15, 45), db_url, holidays=HOLIDAYS)[0] == "skipped"
    swap("price_alerts", lambda now, url: "1 active")
    assert jobs.run_job("price_alerts", at(2026, 11, 11, 16), db_url, holidays=HOLIDAYS) == ("skipped", "market closed")
    assert runs(db_url, "price_alerts") == []                     # no row for every quiet minute
    assert jobs.run_job("rebalance_signals", at(2026, 11, 3, 9, 20), db_url, holidays=HOLIDAYS) == \
        ("skipped", "not the first trading day of the month")


# The page's table: every job with its next and last run.
def test_job_table(db_url, db_session, swap):
    swap("backup", lambda now, url: "marketlens.dump (1.0 MB)")
    jobs.run_job("backup", at(2026, 11, 15), db_url)
    table = {r["id"]: r for r in jobs.job_table(db_url, at(2026, 11, 15, 11), HOLIDAYS)}
    assert len(table) == len(jobs.JOBS) == 10
    assert table["backup"]["status"] == "ok" and table["backup"]["result"].startswith("marketlens.dump")
    assert table["sync_prices"]["status"] == "never run" and table["sync_prices"]["next_run"] == at(2026, 11, 16, 9)


# ---------------------------------------------------------------- each job's work (with fakes)

# 09:00: stocks, ETFs, Nifty 50 and the Momentum 30 ETFs, with 10 years for new symbols.
def test_sync_daily_prices(monkeypatch):
    from marketlens.ingest import prices
    from marketlens.ingest.prices import SyncSummary
    calls = []
    monkeypatch.setattr(prices, "load_universe", lambda name: pd.DataFrame({"symbol": [name.upper()], "name": ["x"]}))
    monkeypatch.setattr(prices, "sync_prices", lambda symbols, start, end, instrument_type, names, url: calls.append(
        (symbols, start, instrument_type)) or SyncSummary(rows_upserted=5))
    text = jobs.sync_daily_prices(at(2026, 11, 11, 9))
    assert [c[2] for c in calls] == ["EQUITY", "ETF", "INDEX", "ETF"] and calls[2][0] == ["^NSEI"]
    assert calls[0][1] == dt.date(2016, 11, 13) and text.startswith("stocks: 5 rows; ETFs: 5 rows")


# Option snapshots and minute data need Dhan; minute data keeps going when one index fails.
def test_dhan_jobs(monkeypatch):
    from marketlens.broker import dhan_client
    from marketlens.ingest import options
    monkeypatch.setattr(dhan_client, "has_keys", lambda: False)
    with pytest.raises(jobs.Skip, match="Dhan keys not set"):
        jobs.snapshot_chains(at(2026, 11, 11))
    with pytest.raises(jobs.Skip):
        jobs.download_minute_data(at(2026, 11, 11, 15, 45))
    monkeypatch.setattr(dhan_client, "has_keys", lambda: True)
    monkeypatch.setattr(options, "snapshot_option_chain", lambda index, url=None: {"rows": 120})
    assert jobs.snapshot_chains(at(2026, 11, 11)) == "NIFTY 120 rows, BANKNIFTY 120 rows"
    asked = []

    def download(index, start, end, flag, strikes, url=None):
        asked.append((index, flag, len(strikes)))
        if index == "SENSEX":
            raise ValueError("Dhan timeout")
        return {"rows": 15_000}
    monkeypatch.setattr(options, "download_expired_options", download)
    text = jobs.download_minute_data(at(2026, 11, 11, 15, 45))
    assert [a[0] for a in asked] == list(jobs.MINUTE_INDICES) and all(a[2] == 21 for a in asked)
    assert "NIFTY (weekly): 15,000 rows" in text and "FAILED SENSEX: Dhan timeout" in text


# 08:30 lists and master; 08:45 login; every minute: alerts.
def test_small_jobs(monkeypatch):
    import scripts.update_universe as uu
    from marketlens.alerts import price_alerts
    from marketlens.broker import dhan_instruments, session
    monkeypatch.setattr(uu, "refresh", lambda name: name == "nifty50")
    monkeypatch.setattr(dhan_instruments, "download_master", lambda: Path("master.csv"))
    text = jobs.refresh_reference_data(at(2026, 11, 11, 8, 30))
    assert "nifty50 refreshed" in text and "kept (download failed)" in text and "Dhan master downloaded" in text
    monkeypatch.setattr(session, "remind_if_needed", lambda now=None, url=None: "⚠️ login")
    assert jobs.check_login(at(2026, 11, 11, 8, 45)) == "login NOT valid: Telegram reminder sent"
    monkeypatch.setattr(price_alerts, "check_alerts", lambda url, now: [{"triggered": True}, {"triggered": False}])
    assert jobs.check_price_alerts(at(2026, 11, 11, 10)) == "2 active, 1 triggered"


# 09:20 first trading day: momentum ranks and ETF weights go to Telegram (saved; no keys in tests).
def test_rebalance_signals(db_url, db_session, monkeypatch):
    from marketlens.alerts import telegram
    from marketlens.momentum import etf_bt, portfolio_bt
    ranks = pd.DataFrame({"symbol": ["TCS", "INFY", "ITC"], "in_top_n": [True, True, False]})
    ranks.attrs["market_ok"] = True
    monkeypatch.setattr(portfolio_bt, "current_ranks", lambda settings, date=None, url=None: ranks)
    monkeypatch.setattr(etf_bt, "load_etf_data", lambda url=None: (_ for _ in ()).throw(ValueError("No stored ETF prices")))
    assert jobs.rebalance_signals(at(2026, 12, 1, 9, 20), db_url) == "2 line(s) sent to Telegram"
    note = telegram.recent_notifications(1, db_url)[0]
    assert note["event"] == "signal" and "hold TCS, INFY" in note["message"] and "not advice" in note["message"]
    assert "no stored ETF prices" in note["message"]


# 15:50 report: P&L, orders, forward test vs backtest and top movers.
def test_eod_report(db_url, db_session):
    gainers = pd.DataFrame({"symbol": ["TCS"], "pct_change": [2.5]})
    losers = pd.DataFrame({"symbol": ["ITC"], "pct_change": [-1.2]})
    text = jobs.eod_report_text(at(2026, 11, 11, 15, 50), db_url, movers=lambda: (gainers, losers))
    assert text.startswith("📊 MarketLens report 11-Nov-2026") and "no forward tests today" in text
    assert "Top movers (Nifty 50): ▲ TCS +2.5% · ▼ ITC -1.2%" in text
    broken = jobs.eod_report_text(at(2026, 11, 11, 15, 50), db_url, movers=lambda: 1 / 0)
    assert "Top movers: not available" in broken


# 16:00: the paper holdings snapshot (nothing open -> 0 rows, still saved).
def test_holdings_snapshot(db_url, db_session, monkeypatch):
    from marketlens.broker import dhan_client
    monkeypatch.setattr(dhan_client, "has_keys", lambda: False)
    assert jobs.holdings_snapshot(at(2026, 11, 11, 16), db_url) == "paper: 0 row(s)"


# Sunday backup: pg_dump with the password in the environment (not the command), newest 8 kept.
def test_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "find_pg_dump", lambda: "pg_dump")
    url = "postgresql+psycopg://marketlens:s3cret@localhost:5433/marketlens"
    for day in range(1, 10):                                   # 9 old backups
        (tmp_path / f"marketlens_2026-10-{day:02d}_1000.dump").write_bytes(b"old")
    (tmp_path / "other_2026-01-01_1000.dump").write_bytes(b"keep")       # another database: untouched
    seen = {}

    def fake_run(command, env, capture_output, text, timeout):
        seen.update(command=command, password=env["PGPASSWORD"])
        Path(next(c for c in command if c.startswith("--file=")).split("=", 1)[1]).write_bytes(b"x" * 2_000_000)
        return subprocess.CompletedProcess(command, 0, "", "")
    text = jobs.backup_database(at(2026, 11, 15), url, tmp_path, run=fake_run)
    assert text.startswith("marketlens_2026-11-15_1000.dump (2.0 MB)") and "removed 2 old backup(s)" in text
    assert seen["password"] == "s3cret" and not any("s3cret" in part for part in seen["command"])
    assert "--port=5433" in seen["command"] and seen["command"][-1] == "marketlens"
    left = sorted(p.name for p in tmp_path.glob("marketlens_*.dump"))
    assert len(left) == 8 and left[-1] == "marketlens_2026-11-15_1000.dump" and (tmp_path / "other_2026-01-01_1000.dump").exists()

    def failing(command, **kw):
        Path(next(c for c in command if c.startswith("--file=")).split("=", 1)[1]).write_bytes(b"half")
        return subprocess.CompletedProcess(command, 1, "", "FATAL: password authentication failed")
    with pytest.raises(RuntimeError, match="password authentication failed"):
        jobs.backup_database(at(2026, 11, 22), url, tmp_path, run=failing)
    assert not (tmp_path / "marketlens_2026-11-22_1000.dump").exists()          # no half-written dump kept
    monkeypatch.setattr(jobs, "find_pg_dump", lambda: None)
    with pytest.raises(RuntimeError, match="pg_dump not found"):
        jobs.backup_database(at(2026, 11, 22), url, tmp_path)


# pg_dump is found from PG_DUMP_PATH first.
def test_find_pg_dump(monkeypatch, tmp_path):
    exe = tmp_path / "pg_dump.exe"
    exe.write_text("")
    monkeypatch.setenv("PG_DUMP_PATH", str(exe))
    assert jobs.find_pg_dump() == str(exe)
    monkeypatch.setenv("PG_DUMP_PATH", str(tmp_path / "missing.exe"))
    assert jobs.find_pg_dump() is None


# ---------------------------------------------------------------- scheduler, command line and page

# Every job (and the heartbeat) is registered in Asia/Kolkata; nothing is started.
def test_build_scheduler():
    scheduler = jobs.build_scheduler(blocking=False)
    ids = {j.id for j in scheduler.get_jobs()}
    assert ids == set(jobs.BY_ID) | {"heartbeat"}
    assert str(scheduler.timezone) == "Asia/Kolkata"
    assert all(j.max_instances == 1 for j in scheduler.get_jobs() if j.id != "heartbeat")


# --list and --run.
def test_command_line(db_url, db_session, swap, capsys):
    assert cli.main(["--list"], url=db_url) == 0
    out = capsys.readouterr().out
    assert "reference_data" in out and "never run" in out and len(out.strip().splitlines()) == 10
    swap("backup", lambda now, url: "done")
    assert cli.main(["--run", "backup"], url=db_url) == 0 and "backup: ok - done" in capsys.readouterr().out
    swap("backup", lambda now, url: 1 / 0)
    assert cli.main(["--run", "backup"], url=db_url) == 1


# The Settings page lists every job and runs one on request.
def test_settings_page(db_url, db_session, monkeypatch):
    from marketlens.alerts import price_alerts
    monkeypatch.setattr(config, "DATABASE_URL", db_url)
    monkeypatch.setattr(price_alerts, "check_alerts", lambda url, now: [])
    at_ = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at_.exception
    table = at_.dataframe[-1].value
    assert len(table) == 10 and set(table["status"]) == {"never run"}
    assert any("Scheduler not started" in w.value for w in at_.warning)
    at_.selectbox(key="sched_pick").select("price_alerts")
    at_.button(key="sched_run").click().run()
    assert at_.success[-1].value.startswith("ok: no active alerts")
    assert "✅ ok" in set(at_.dataframe[-1].value["status"])
