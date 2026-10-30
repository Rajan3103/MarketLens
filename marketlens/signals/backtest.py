"""Backtest and scan indicator rules (module 7).

NO LOOK-AHEAD: a rule is checked when a candle CLOSES, so the order fills at the NEXT candle's
open (like TradingView's default). Stop loss and target are orders waiting in the market, so
they are checked on each candle's high / low; if both are touched in one candle the SL is
assumed (the worse case). Intraday strategies close at the last candle of each day.
Costs (brokerage, taxes, slippage) come from costs.py through the Trade class.

    candles = load_candles("RELIANCE", "1 day", start, end)
    result, markers = run_signal_backtest(strategy, candles)
"""
import datetime as dt

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from marketlens.backtest.engine import BacktestResult, Trade
from marketlens.broker.candles import IST
from marketlens.logger import get_logger
from marketlens.signals.rules import ON_PRICE, SignalStrategy, evaluate

log = get_logger(__name__)
CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]
BOTH_HIT = "SL and target both touched in this candle: assumed SL (worse case)"


# ---------------------------------------------------------------- candles

# Tidy any candle table: time index (India time), columns open/high/low/close/volume, sorted.
def tidy(frame: pd.DataFrame, time_column: str | None = None) -> pd.DataFrame:
    df = frame.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if time_column:
        df = df.set_index(time_column.lower())
    index = pd.DatetimeIndex(pd.to_datetime(df.index))
    df.index = index.tz_localize(IST) if index.tz is None else index.tz_convert(IST)
    df.index.name = "timestamp"
    if "volume" not in df:
        df["volume"] = 0
    return df[CANDLE_COLUMNS].astype(float).dropna(subset=["open", "high", "low", "close"]).sort_index()


# Daily candles stored by sync_prices (daily_prices table), one table per symbol.
def stored_daily(symbols: list[str], start: dt.date, end: dt.date, url: str | None = None) -> dict:
    from sqlalchemy import Float, cast, select

    from marketlens.db import get_session
    from marketlens.models import DailyPrice, Instrument
    symbols = [s.strip().upper() for s in symbols]
    with get_session(url) as session:
        rows = session.execute(
            select(Instrument.symbol, DailyPrice.date, cast(DailyPrice.open, Float), cast(DailyPrice.high, Float),
                   cast(DailyPrice.low, Float), cast(DailyPrice.close, Float), DailyPrice.volume)
            .join(Instrument, Instrument.id == DailyPrice.instrument_id)
            .where(Instrument.symbol.in_(symbols), DailyPrice.date.between(start, end))).all()
    if not rows:
        return {}
    table = pd.DataFrame(rows, columns=["symbol", "date", *CANDLE_COLUMNS])
    return {sym: tidy(part.drop(columns="symbol"), "date") for sym, part in table.groupby("symbol")}


# Candles for one instrument and timeframe. Daily: stored prices, else yfinance, else Dhan / DEMO.
# Intraday: Dhan (DEMO without keys). attrs["source"] says where they came from.
def load_candles(symbol: str, timeframe: str, start: dt.date, end: dt.date, client=None,
                 url: str | None = None) -> pd.DataFrame:
    from marketlens.signals.rules import TIMEFRAMES
    symbol, minutes = symbol.strip().upper(), TIMEFRAMES[timeframe]
    if minutes == 1440:
        try:
            stored = stored_daily([symbol], start, end, url).get(symbol)
            if stored is not None and len(stored) > 20:
                stored.attrs["source"] = "database (yfinance)"
                return stored
        except Exception as err:
            log.warning("Stored prices not available: %s", err)
        try:
            from marketlens.ingest.prices import download_prices, ticker_frame, yf_ticker
            ticker = yf_ticker(symbol)
            frame = ticker_frame(download_prices([ticker], start, end), ticker)
            if not frame.empty:
                out = tidy(frame)
                out.attrs["source"] = "yfinance"
                return out
        except Exception as err:
            log.warning("yfinance not available for %s: %s", symbol, err)
    if client is None:
        from marketlens.broker.market_data import get_data_client
        client = get_data_client()
    frame = client.get_candles(symbol, start, end, minutes)
    out = tidy(frame, "timestamp") if not frame.empty else pd.DataFrame(columns=CANDLE_COLUMNS)
    out.attrs["source"] = "DEMO" if getattr(client, "is_demo", False) else "Dhan"
    return out


# ---------------------------------------------------------------- backtest

# Run the rules over the candles; returns the BacktestResult and a table of entry / exit markers.
def run_signal_backtest(strategy: SignalStrategy, candles: pd.DataFrame) -> tuple[BacktestResult, pd.DataFrame]:
    s = strategy
    candles = candles[(candles.index.date >= s.start) & (candles.index.date <= s.end)] if len(candles) else candles
    if len(candles) < 2:
        raise ValueError("Not enough candles in this date range")
    enter = evaluate(s.entry, s.entry_join, candles).to_numpy()
    leave = evaluate(s.exit, s.exit_join, candles).to_numpy() if s.exit else np.zeros(len(candles), bool)
    o, h, low, c = (candles[k].to_numpy() for k in ("open", "high", "low", "close"))
    times = candles.index
    days = np.array(times.date)
    last_of_day = np.r_[days[1:] != days[:-1], True] if s.intraday else np.zeros(len(candles), bool)
    long = s.side == "long"
    side, close_side = ("BUY", "SELL") if long else ("SELL", "BUY")
    segment = "equity_intraday" if s.intraday else "equity_delivery"
    step = pd.Timedelta(minutes=s.minutes) if s.intraday else pd.Timedelta(0)

    trades, markers = [], []
    position = None                     # dict(entry, time, sl, target, why)
    pending = None                      # "enter" or "exit" at the next candle's open

    # Close the open position at `price` (time `when`).
    def close(price, when, reason, note=None):
        nonlocal position
        trades.append(Trade(s.symbol, side, s.qty, position["time"], position["entry"], when, float(price),
                            segment=segment, leg_no=1, exit_reason=reason, note=note or position["why"],
                            settings=s.costs))
        markers.append({"time": when, "price": float(price), "kind": "exit", "reason": reason})
        position = None

    for i in range(len(candles)):
        # 1. orders decided at the previous close fill at this open
        if pending == "exit" and position:
            close(o[i], times[i], "SIGNAL")
        elif pending == "enter" and position is None:
            entry = float(o[i])
            sign = 1 if long else -1
            position = {"entry": entry, "time": times[i], "why": f"entry rule true at the {times[i - 1]:%d-%b-%Y %H:%M} close",
                        "sl": entry * (1 - sign * s.sl_pct / 100) if s.sl_pct else None,
                        "target": entry * (1 + sign * s.target_pct / 100) if s.target_pct else None}
            markers.append({"time": times[i], "price": entry, "kind": "entry", "reason": side})
        pending = None
        # 2. stop loss / target inside this candle
        if position:
            sl, tg = position["sl"], position["target"]
            sl_hit = sl is not None and (low[i] <= sl if long else h[i] >= sl)
            tg_hit = tg is not None and (h[i] >= tg if long else low[i] <= tg)
            if sl_hit:
                gap = o[i] <= sl if long else o[i] >= sl
                close(o[i] if gap else sl, times[i], "SL", BOTH_HIT if tg_hit else None)
            elif tg_hit:
                gap = o[i] >= tg if long else o[i] <= tg
                close(o[i] if gap else tg, times[i], "TARGET")
        # 3. at this candle's close
        if position and last_of_day[i]:
            close(c[i], times[i] + step, "DAY_END")
        elif position and leave[i] and i + 1 < len(candles):
            pending = "exit"
        elif position is None and enter[i] and i + 1 < len(candles) and not last_of_day[i]:
            pending = "enter"
    if position:
        close(c[-1], times[-1] + step, "END", "still open when the data ended")

    daily_close = candles["close"].groupby(candles.index.date).last()
    result = BacktestResult.build(trades, s.capital, s.start, s.end, name=s.name, benchmark=daily_close,
                                  benchmark_name=f"{s.symbol} (buy and hold)", params=s.to_params(),
                                  is_demo=candles.attrs.get("source") == "DEMO")
    result.extras = {"source": candles.attrs.get("source", ""), "candles": len(candles)}
    return result, pd.DataFrame(markers, columns=["time", "price", "kind", "reason"])


# Price chart with the rules' indicators and the entry / exit markers.
def signal_chart(strategy: SignalStrategy, candles: pd.DataFrame, markers: pd.DataFrame) -> go.Figure:
    on_price = [op for op in strategy.operands() if op.kind in ON_PRICE]
    below = [op for op in strategy.operands() if op.kind not in ON_PRICE]
    rows = 2 if below else 1
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3][:rows], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=candles.index, open=candles["open"], high=candles["high"], low=candles["low"],
                                 close=candles["close"], name=strategy.symbol), row=1, col=1)
    colours = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e87ba4", "#eda100"]
    for n, op in enumerate(on_price):
        fig.add_trace(go.Scatter(x=candles.index, y=op.series(candles), name=op.label(),
                                 line=dict(width=1.5, color=colours[n % len(colours)])), row=1, col=1)
    for n, op in enumerate(below):
        fig.add_trace(go.Scatter(x=candles.index, y=op.series(candles), name=op.label(),
                                 line=dict(width=1.5, color=colours[(n + len(on_price)) % len(colours)])), row=2, col=1)
    for kind, symbol, colour in (("entry", "triangle-up" if strategy.side == "long" else "triangle-down", "#1baf7a"),
                                 ("exit", "x", "#e34948")):
        part = markers[markers["kind"] == kind] if len(markers) else markers
        if len(part):
            fig.add_trace(go.Scatter(x=part["time"], y=part["price"], mode="markers", name=kind.title(),
                                     marker=dict(symbol=symbol, size=11, color=colour, line=dict(width=1, color="white")),
                                     text=part["reason"], hovertemplate="%{text} at %{y:,.2f}<extra></extra>"),
                          row=1, col=1)
    fig.update_layout(height=560 if rows == 2 else 460, xaxis_rangeslider_visible=False, legend=dict(orientation="h"),
                      margin=dict(l=10, r=10, t=30, b=10))
    return fig


# ---------------------------------------------------------------- scanner

# Where the entry rule is true on the latest daily candle, for many symbols. Historical signal only.
def scan(strategy: SignalStrategy, symbols: list[str], days: int = 400, url: str | None = None,
         candles: dict | None = None) -> pd.DataFrame:
    """candles: {symbol: table} to scan given data (tests); otherwise stored daily prices are used,
    and yfinance for symbols that are not stored."""
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    if candles is None:
        candles = stored_daily(symbols, start, end, url)
        missing = [s for s in symbols if s.upper() not in candles]
        if missing:
            try:
                from marketlens.ingest.prices import download_prices, ticker_frame, yf_ticker
                data = download_prices([yf_ticker(s) for s in missing], start, end)
                for sym in missing:
                    frame = ticker_frame(data, yf_ticker(sym))
                    if not frame.empty:
                        candles[sym.upper()] = tidy(frame)
            except Exception as err:
                log.warning("yfinance not available for the scan: %s", err)
    rows = []
    for sym in symbols:
        frame = candles.get(sym.upper())
        if frame is None or len(frame) < 2:
            rows.append({"symbol": sym.upper(), "date": None, "close": None, "signal": None, "note": "no data"})
            continue
        hit = evaluate(strategy.entry, strategy.entry_join, frame)
        row = {"symbol": sym.upper(), "date": frame.index[-1].date(), "close": round(float(frame["close"].iloc[-1]), 2),
               "signal": bool(hit.iloc[-1]), "note": ""}
        for op in strategy.operands():
            row[op.label()] = round(float(op.series(frame).iloc[-1]), 2)
        rows.append(row)
    table = pd.DataFrame(rows)
    return table.sort_values(["signal", "symbol"], ascending=[False, True]).reset_index(drop=True)
