"""Tests for marketlens/alerts/price_alerts.py. Market data and Telegram are fakes (no network);
the alerts live in TEST_DATABASE_URL."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from marketlens import config
from marketlens.alerts import price_alerts as pa
from marketlens.alerts import telegram
from marketlens.broker.candles import IST

NOW = dt.datetime(2026, 11, 3, 11, 0, tzinfo=IST)
RSI_BELOW_30 = {"conditions": [{"left": {"kind": "rsi", "length": 14}, "op": "<",
                                "right": {"kind": "number", "value": 30}}], "join": "AND"}


# Candles that fall every day (RSI near 0) or rise every day (RSI near 100).
def candles(falling: bool, days: int = 60) -> pd.DataFrame:
    close = np.linspace(200, 100, days) if falling else np.linspace(100, 200, days)
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000},
                        index=pd.bdate_range("2026-08-03", periods=days))


# An option chain with total call OI 1,000,000 and put OI 1,500,000 (PCR 1.5).
def chain() -> pd.DataFrame:
    return pd.DataFrame({"strike": [24900, 25000, 25100], "ce_oi": [200_000, 300_000, 500_000],
                         "pe_oi": [700_000, 500_000, 300_000]})


class Sent:
    """A fake notify that records the texts."""
    def __init__(self):
        self.texts = []

    def __call__(self, text, source=None, event=None, url=None):
        self.texts.append((text, source, event))
        return True


@pytest.fixture
def fakes():
    return {"quotes": lambda symbols: {"NIFTY": (25_520.0, 25_300.0), "RELIANCE": (1_470.0, 1_500.0),
                                       "TCS": (3_500.0, 3_480.0)},
            "candles": lambda symbol: candles(falling=symbol == "TCS"),
            "chains": lambda index: (chain(), "DEMO" if index == "BANKNIFTY" else "DHAN")}


# ---------------------------------------------------------------- saving

# Add, list (with readable descriptions) and delete alerts.
def test_add_list_delete(db_url, db_session):
    a = pa.add_alert("nifty", "above", 25_500, note="breakout", url=db_url)
    b = pa.add_alert("RELIANCE", "CHANGE_BELOW", -2, url=db_url)
    c = pa.add_alert("TCS", "INDICATOR", rule=RSI_BELOW_30, url=db_url)
    d = pa.add_alert("BANKNIFTY", "OI_ABOVE", 5e6, side="pe", url=db_url)
    e = pa.add_alert("NIFTY", "PCR_BELOW", 0.8, url=db_url)
    table = pa.list_alerts(url=db_url).set_index("id")
    assert table.loc[a, "description"] == "NIFTY price above 25,500.00" and table.loc[a, "note"] == "breakout"
    assert table.loc[b, "description"] == "RELIANCE % change today below -2 %"
    assert table.loc[c, "description"] == "TCS: RSI(14) < 30" and pd.isna(table.loc[c, "target"])
    assert table.loc[d, "description"] == "BANKNIFTY put open interest above 5,000,000"
    assert table.loc[e, "description"] == "NIFTY put-call ratio below 0.8"
    assert pa.delete_alert(a, url=db_url) is True and pa.delete_alert(a, url=db_url) is False
    assert a not in set(pa.list_alerts(url=db_url)["id"])


# Bad alerts are refused with a clear message.
@pytest.mark.parametrize("args, words", [
    (("", "ABOVE", 10), "Choose a symbol"), (("NIFTY", "SIDEWAYS", 10), "condition must be one of"),
    (("NIFTY", "ABOVE", None), "give the level"), (("NIFTY", "BELOW", -5), "above 0"),
    (("RELIANCE", "PCR_ABOVE", 1.2), "NIFTY, BANKNIFTY"), (("NIFTY", "INDICATOR", None), "at least one condition"),
])
def test_bad_alerts(args, words):
    with pytest.raises(ValueError, match=words):
        pa.clean_alert(*args)
    with pytest.raises(ValueError, match="side must be"):
        pa.clean_alert("NIFTY", "OI_ABOVE", 1, side="both")
    with pytest.raises(ValueError, match="Unknown operator"):
        pa.clean_alert("TCS", "INDICATOR", rule={"conditions": [{"left": {"kind": "rsi"}, "op": "~",
                                                                 "right": {"kind": "number"}}]})


# ---------------------------------------------------------------- checking

# One check: price, % change, indicator, PCR and OI alerts; the hit ones are told and switched off.
def test_check_alerts(db_url, db_session, fakes):
    above = pa.add_alert("NIFTY", "ABOVE", 25_500, url=db_url)             # 25,520 -> hit
    below = pa.add_alert("NIFTY", "BELOW", 25_000, url=db_url)             # not hit
    drop = pa.add_alert("RELIANCE", "CHANGE_BELOW", -1.5, url=db_url)      # -2 % -> hit
    rsi_low = pa.add_alert("TCS", "INDICATOR", rule=RSI_BELOW_30, url=db_url)        # falling -> hit
    rsi_quiet = pa.add_alert("INFY", "INDICATOR", rule=RSI_BELOW_30, url=db_url)     # rising -> not hit
    pcr_high = pa.add_alert("NIFTY", "PCR_ABOVE", 1.3, url=db_url)         # 1.5 -> hit
    oi_demo = pa.add_alert("BANKNIFTY", "OI_ABOVE", 1_000, side="ce", url=db_url)    # DEMO chain -> never
    sent = Sent()
    results = {r["id"]: r for r in pa.check_alerts(db_url, NOW, notify=sent, **fakes)}
    assert {i for i, r in results.items() if r["triggered"]} == {above, drop, rsi_low, pcr_high}
    assert results[drop]["value"] == -2.0 and results[pcr_high]["value"] == 1.5
    assert results[oi_demo]["status"] == "demo" and results[oi_demo]["value"] == 1_000_000
    assert results[below]["status"] == "checked" and results[rsi_quiet]["triggered"] is False
    texts = [t for t, _, _ in sent.texts]
    assert len(texts) == 4 and all(s == "price_alert" and e == "price_alert" for _, s, e in sent.texts)
    assert "🔔 Price alert: NIFTY price above 25,500.00 (now 25,520.00)" in texts
    assert any("RELIANCE % change today below -1.5 % (now -2.00 %)" in t for t in texts)
    assert any("TCS: RSI(14) < 30 (close" in t for t in texts)

    table = pa.list_alerts(url=db_url).set_index("id")
    assert not table.loc[above, "is_active"] and table.loc[above, "trigger_message"].startswith("🔔")
    assert table.loc[below, "is_active"] and table.loc[below, "last_value"] == 25_520
    assert set(pa.list_alerts(active_only=True, url=db_url)["id"]) == {below, rsi_quiet, oi_demo}
    again = pa.check_alerts(db_url, NOW, notify=sent, **fakes)                # triggered ones are not told again
    assert len(sent.texts) == 4 and {r["id"] for r in again} == {below, rsi_quiet, oi_demo}


# A data source that fails only affects its own alerts ("no data"); nothing is triggered by mistake.
def test_check_with_missing_data(db_url, db_session, fakes):
    price = pa.add_alert("NIFTY", "ABOVE", 1, url=db_url)
    unknown = pa.add_alert("NOSUCH", "ABOVE", 1, url=db_url)
    pcr = pa.add_alert("NIFTY", "PCR_ABOVE", 0.1, url=db_url)

    def broken(index):
        raise ConnectionError("Dhan down")
    results = {r["id"]: r for r in pa.check_alerts(db_url, NOW, notify=Sent(), quotes=fakes["quotes"],
                                                   candles=fakes["candles"], chains=broken)}
    assert results[price]["triggered"] and results[unknown]["status"] == "no data"
    assert results[pcr]["status"] == "no data" and not results[pcr]["triggered"]


# The real sender: a triggered alert goes to Telegram (faked) and is saved in notifications as sent.
def test_triggered_alert_reaches_telegram(db_url, db_session, fakes, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "1:abc")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "5")
    posted = []
    monkeypatch.setattr(telegram, "_post", lambda message, timeout: posted.append(message["text"]) or {"ok": True})
    pa.add_alert("NIFTY", "ABOVE", 25_000, url=db_url)
    pa.check_alerts(db_url, NOW, **fakes)
    assert posted and posted[0].startswith("🔔 Price alert: NIFTY")
    row = telegram.recent_notifications(1, db_url)[0]
    assert (row["source"], row["event"], row["status"]) == ("price_alert", "price_alert", "sent")


# ---------------------------------------------------------------- market data helpers

# Quotes from yfinance when Dhan is not set up (index names become Yahoo tickers); from Dhan when it is.
def test_latest_quotes(monkeypatch):
    from marketlens import overview
    from marketlens.broker import dhan_data

    class NoDhan:
        def is_ready(self):
            return False
    monkeypatch.setattr(dhan_data, "DhanDataClient", NoDhan)
    asked = []
    days = pd.to_datetime(["2026-11-02", "2026-11-03"])
    data = pd.concat({t: pd.DataFrame({"Close": [100.0, 103.0]}, index=days) for t in ("^NSEI", "TCS.NS")}, axis=1)
    monkeypatch.setattr(overview, "_yf_closes", lambda tickers: asked.append(tickers) or data)
    assert pa.latest_quotes(["NIFTY", "TCS"]) == {"NIFTY": (103.0, 100.0), "TCS": (103.0, 100.0)}
    assert asked[0] == ["TCS.NS", "^NSEI"]

    class Dhan:
        def is_ready(self):
            return True

        def get_quote(self, symbols):
            return pd.DataFrame({"symbol": symbols, "ltp": 10.5, "close": 10.0})
    monkeypatch.setattr(dhan_data, "DhanDataClient", Dhan)
    assert pa.latest_quotes(["RELIANCE"]) == {"RELIANCE": (10.5, 10.0)}


# The command line lists, checks (with fakes behind it) and deletes.
def test_command_line(db_url, db_session, monkeypatch, capsys):
    alert = pa.add_alert("NIFTY", "ABOVE", 30_000, url=db_url)
    assert pa.main(["list"], url=db_url) == 0 and "NIFTY price above 30,000.00" in capsys.readouterr().out
    monkeypatch.setattr(pa, "latest_quotes", lambda symbols: {"NIFTY": (25_000.0, 24_900.0)})
    assert pa.main(["check"], url=db_url) == 0 and "checked 25000.0" in capsys.readouterr().out
    assert pa.main(["delete", str(alert)], url=db_url) == 0 and "Deleted." in capsys.readouterr().out
