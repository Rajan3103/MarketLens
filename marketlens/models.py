"""All MarketLens database tables (PostgreSQL only).

Each class below is one table. Each `name: Mapped[type]` line is one column.
`Mapped[X | None]` means the column may be empty (NULL).

Column types used everywhere:
  - prices      -> NUMERIC(14, 4)  exact decimals, never rounding errors like 0.1 + 0.2
  - money (Rs)  -> NUMERIC(16, 2)
  - timestamps  -> TIMESTAMPTZ     stored with the time zone, shown in Asia/Kolkata
  - settings    -> JSONB           flexible key/value settings, searchable in PostgreSQL

After changing this file, make a new migration:
    alembic revision --autogenerate -m "what changed"
    alembic upgrade head
"""
import datetime as dt
from decimal import Decimal

from sqlalchemy import (BigInteger, CheckConstraint, DateTime, ForeignKey, Index, MetaData,
                        Numeric, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Fixed names for keys and indexes, so Alembic migrations stay tidy
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Money = Numeric(16, 2)    # rupee amounts: P&L, capital
Pct = Numeric(10, 4)      # percentages and ratios: IV, CAGR, win rate, weights
Greek = Numeric(12, 6)    # option Greeks need more decimals
EMPTY_JSON = text("'{}'::jsonb")
EMPTY_LIST = text("'[]'::jsonb")


class Base(DeclarativeBase):
    """Parent of every table. Sets the default column type for each Python type."""
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {
        dt.datetime: DateTime(timezone=True),   # TIMESTAMPTZ
        Decimal: Numeric(14, 4),                # prices
        dict: JSONB,
        list: JSONB,
    }


# Make a rule that a text column may only hold one of the listed words.
def one_of(column: str, values: list[str]) -> CheckConstraint:
    """Example: one_of("side", ["BUY", "SELL"]) blocks typos like "BYU"."""
    words = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({words})", name=f"{column}_valid")


# Column that fills itself with the current time when a row is added.
def created_now():
    """Shortcut for the many created_at / received_at columns."""
    return mapped_column(server_default=func.now())


# =====================================================================
# Market data
# =====================================================================

class Instrument(Base):
    """Every stock, ETF, index and futures contract we track: RELIANCE, NIFTYBEES, NIFTY,
    SENSEX, NIFTY-Nov2026-FUT..."""
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str]                       # e.g. RELIANCE, NIFTY
    exchange: Mapped[str] = mapped_column(server_default="NSE")
    instrument_type: Mapped[str]              # EQUITY, ETF, INDEX or FUTURES
    name: Mapped[str | None]
    yf_ticker: Mapped[str | None]             # e.g. RELIANCE.NS, ^NSEI
    dhan_security_id: Mapped[str | None]      # Dhan's id for this instrument
    segment: Mapped[str | None]               # Dhan exchange segment: IDX_I, NSE_EQ, NSE_FNO, BSE_FNO
    expiry: Mapped[dt.date | None]            # futures only
    lot_size: Mapped[int | None]
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[dt.datetime] = created_now()
    __table_args__ = (
        UniqueConstraint("symbol", "exchange"),
        one_of("exchange", ["NSE", "BSE"]),
        one_of("instrument_type", ["EQUITY", "ETF", "INDEX", "FUTURES"]),
    )


class DailyPrice(Base):
    """One row per instrument per trading day (from yfinance or Dhan)."""
    __tablename__ = "daily_prices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    date: Mapped[dt.date]
    open: Mapped[Decimal]
    high: Mapped[Decimal]
    low: Mapped[Decimal]
    close: Mapped[Decimal]
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(server_default="yfinance")
    # The unique key also works as the (instrument_id, date) index
    __table_args__ = (UniqueConstraint("instrument_id", "date"),)


class PriceBar(Base):
    """Candles downloaded from Dhan for indices, stocks and futures, at any timeframe.

    timeframe is in minutes: 1, 5, 15, 25, 60, or 1440 for daily candles.
    (daily_prices keeps the free yfinance history; the two are never mixed.)
    """
    __tablename__ = "price_bars"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    timeframe: Mapped[int]
    ts: Mapped[dt.datetime]                   # start time of the candle (IST)
    open: Mapped[Decimal]
    high: Mapped[Decimal]
    low: Mapped[Decimal]
    close: Mapped[Decimal]
    volume: Mapped[int | None] = mapped_column(BigInteger)
    oi: Mapped[int | None] = mapped_column(BigInteger)   # open interest (futures)
    is_demo: Mapped[bool] = mapped_column(server_default=text("false"))  # made-up DEMO data
    # The unique key also works as the (instrument_id, timeframe, ts) index
    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "ts"),
        CheckConstraint("timeframe IN (1, 5, 15, 25, 60, 1440)", name="timeframe_valid"),
    )


class OptionContract(Base):
    """One option: underlying + expiry + strike + CE/PE, e.g. NIFTY 30-Oct 25000 CE."""
    __tablename__ = "option_contracts"
    id: Mapped[int] = mapped_column(primary_key=True)
    underlying_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    expiry: Mapped[dt.date] = mapped_column(index=True)
    strike: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    option_type: Mapped[str]                  # CE (call) or PE (put)
    trading_symbol: Mapped[str | None]
    dhan_security_id: Mapped[str | None]
    lot_size: Mapped[int | None]
    is_demo: Mapped[bool] = mapped_column(server_default=text("false"))  # DEMO contract and prices
    created_at: Mapped[dt.datetime] = created_now()
    __table_args__ = (
        UniqueConstraint("underlying_id", "expiry", "strike", "option_type"),
        one_of("option_type", ["CE", "PE"]),
    )


class OptionQuote(Base):
    """Snapshot of one option at one moment: price, OI, IV and Greeks (option chain)."""
    __tablename__ = "option_quotes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("option_contracts.id", ondelete="CASCADE"))
    ts: Mapped[dt.datetime]
    ltp: Mapped[Decimal]                      # last traded price
    bid: Mapped[Decimal | None]
    ask: Mapped[Decimal | None]
    volume: Mapped[int | None] = mapped_column(BigInteger)
    oi: Mapped[int | None] = mapped_column(BigInteger)   # open interest
    iv: Mapped[Decimal | None] = mapped_column(Pct)      # implied volatility
    delta: Mapped[Decimal | None] = mapped_column(Greek)
    gamma: Mapped[Decimal | None] = mapped_column(Greek)
    theta: Mapped[Decimal | None] = mapped_column(Greek)
    vega: Mapped[Decimal | None] = mapped_column(Greek)
    underlying_price: Mapped[Decimal | None]
    __table_args__ = (UniqueConstraint("contract_id", "ts"),)


class IntradayOptionPrice(Base):
    """Minute bars for expired and live options (used by the backtester and simulator)."""
    __tablename__ = "intraday_option_prices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("option_contracts.id", ondelete="CASCADE"))
    ts: Mapped[dt.datetime]
    open: Mapped[Decimal]
    high: Mapped[Decimal]
    low: Mapped[Decimal]
    close: Mapped[Decimal]
    volume: Mapped[int | None] = mapped_column(BigInteger)
    oi: Mapped[int | None] = mapped_column(BigInteger)
    iv: Mapped[Decimal | None] = mapped_column(Pct)
    # The unique key also works as the (contract_id, ts) index
    __table_args__ = (UniqueConstraint("contract_id", "ts"),)


# =====================================================================
# Strategies and backtests
# =====================================================================

class Strategy(Base):
    """A saved strategy (Strategy Library, module 10)."""
    __tablename__ = "strategies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str]                         # options, indicator, momentum or etf
    description: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    tags: Mapped[list] = mapped_column(server_default=EMPTY_LIST)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[dt.datetime] = created_now()
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    __table_args__ = (one_of("kind", ["options", "indicator", "momentum", "etf"]),)


class StrategyLeg(Base):
    """One leg of an options strategy, e.g. 'SELL 1 lot ATM CE, weekly, SL 25%'."""
    __tablename__ = "strategy_legs"
    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    leg_no: Mapped[int]
    action: Mapped[str]                       # BUY or SELL
    instrument_kind: Mapped[str]              # CE, PE, FUT or EQ
    strike_rule: Mapped[str] = mapped_column(server_default="ATM")   # ATM, ATM+2, PREMIUM:50
    expiry_rule: Mapped[str] = mapped_column(server_default="WEEKLY")  # WEEKLY, NEXT_WEEKLY, MONTHLY
    lots: Mapped[int] = mapped_column(server_default=text("1"))
    stop_loss_pct: Mapped[Decimal | None] = mapped_column(Pct)
    target_pct: Mapped[Decimal | None] = mapped_column(Pct)
    params: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    __table_args__ = (
        UniqueConstraint("strategy_id", "leg_no"),
        one_of("action", ["BUY", "SELL"]),
        one_of("instrument_kind", ["CE", "PE", "FUT", "EQ"]),
    )


class BacktestRun(Base):
    """One run of the backtester, with its settings and summary results."""
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="SET NULL"), index=True)
    params: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    period_start: Mapped[dt.date]
    period_end: Mapped[dt.date]
    status: Mapped[str] = mapped_column(server_default="running")
    total_pnl: Mapped[Decimal | None] = mapped_column(Money)
    cagr: Mapped[Decimal | None] = mapped_column(Pct)
    max_drawdown: Mapped[Decimal | None] = mapped_column(Pct)
    sharpe: Mapped[Decimal | None] = mapped_column(Pct)
    win_rate: Mapped[Decimal | None] = mapped_column(Pct)
    trades_count: Mapped[int | None]
    metrics: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)   # anything else
    started_at: Mapped[dt.datetime] = created_now()
    finished_at: Mapped[dt.datetime | None]
    __table_args__ = (one_of("status", ["running", "done", "failed"]),)


class BacktestTrade(Base):
    """Every trade inside a backtest run."""
    __tablename__ = "backtest_trades"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"))
    leg_no: Mapped[int | None]
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("option_contracts.id"))
    symbol: Mapped[str | None]                # e.g. "NIFTY 25000 CE 03-Nov-2026"
    side: Mapped[str]
    qty: Mapped[int]
    entry_time: Mapped[dt.datetime]
    entry_price: Mapped[Decimal]
    exit_time: Mapped[dt.datetime | None]
    exit_price: Mapped[Decimal | None]
    pnl: Mapped[Decimal | None] = mapped_column(Money)            # after costs
    costs: Mapped[Decimal | None] = mapped_column(Money)          # brokerage + taxes + slippage
    exit_reason: Mapped[str | None]           # SL, TARGET, TRAIL_SL, COST_SL, M2M_SL, M2M_TARGET, M2M_TRAIL, TIME
    note: Mapped[str | None] = mapped_column(Text)   # e.g. "SL and target both touched: assumed SL"
    __table_args__ = (
        Index("ix_backtest_trades_run_entry", "run_id", "entry_time"),
        one_of("side", ["BUY", "SELL"]),
    )


class ForwardTestRun(Base):
    """A strategy running on the live market with virtual money (module 6)."""
    __tablename__ = "forward_test_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(server_default="running")
    capital: Mapped[Decimal] = mapped_column(Money)
    pnl: Mapped[Decimal | None] = mapped_column(Money)
    params: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = created_now()
    stopped_at: Mapped[dt.datetime | None]
    __table_args__ = (one_of("status", ["running", "stopped"]),)


class SimulatorSession(Base):
    """One practice session replaying a past trading day (module 5)."""
    __tablename__ = "simulator_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    underlying_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    trade_date: Mapped[dt.date]               # the past day being replayed
    capital: Mapped[Decimal] = mapped_column(Money)
    pnl: Mapped[Decimal | None] = mapped_column(Money)
    settings: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = created_now()
    ended_at: Mapped[dt.datetime | None]


class SimulatorTrade(Base):
    """A virtual trade placed during a simulator session."""
    __tablename__ = "simulator_trades"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("simulator_sessions.id", ondelete="CASCADE"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("option_contracts.id"))
    symbol: Mapped[str | None]                # e.g. "NIFTY 25000 CE 03-Nov-2026"
    side: Mapped[str]
    qty: Mapped[int]
    price: Mapped[Decimal]
    market_time: Mapped[dt.datetime]          # the replayed market time, not today's clock
    pnl: Mapped[Decimal | None] = mapped_column(Money)            # on the closing order, after costs
    costs: Mapped[Decimal | None] = mapped_column(Money)          # costs.py: brokerage + taxes of this order
    reason: Mapped[str | None]                # OPEN, MANUAL, SL, TARGET, EXIT_ALL, DAY_END
    created_at: Mapped[dt.datetime] = created_now()
    __table_args__ = (
        Index("ix_simulator_trades_session_time", "session_id", "market_time"),
        one_of("side", ["BUY", "SELL"]),
    )


# =====================================================================
# Portfolios
# =====================================================================

class PortfolioHolding(Base):
    """What a momentum or ETF strategy holds right now."""
    __tablename__ = "portfolio_holdings"
    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    qty: Mapped[Decimal]
    avg_price: Mapped[Decimal]
    weight: Mapped[Decimal | None] = mapped_column(Pct)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("strategy_id", "instrument_id"),)


class HoldingsSnapshot(Base):
    """Holdings saved at each rebalance date, to draw the portfolio's history."""
    __tablename__ = "holdings_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    snapshot_date: Mapped[dt.date]
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    qty: Mapped[Decimal]
    price: Mapped[Decimal]
    value: Mapped[Decimal] = mapped_column(Money)
    weight: Mapped[Decimal | None] = mapped_column(Pct)
    __table_args__ = (UniqueConstraint("strategy_id", "snapshot_date", "instrument_id"),)


# =====================================================================
# Signals, orders and positions
# =====================================================================

class Signal(Base):
    """A buy/sell/exit signal from a backtest, forward test, live bot or webhook."""
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("option_contracts.id"))
    side: Mapped[str]
    price: Mapped[Decimal | None]
    source: Mapped[str]
    reason: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    created_at: Mapped[dt.datetime] = created_now()
    __table_args__ = (
        Index("ix_signals_strategy_ts", "strategy_id", "ts"),
        one_of("side", ["BUY", "SELL", "EXIT"]),
        one_of("source", ["backtest", "forward", "live", "webhook", "manual"]),
    )


class Order(Base):
    """Every order, paper or live. Live orders also carry Dhan's order id."""
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mode: Mapped[str] = mapped_column(server_default="paper")
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id", ondelete="SET NULL"))
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    forward_test_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("forward_test_runs.id", ondelete="SET NULL"))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("option_contracts.id"))
    symbol: Mapped[str | None] = mapped_column(index=True)    # e.g. "RELIANCE" or "NIFTY 25000 CE 03-Nov-2026"
    security_id: Mapped[str | None]           # Dhan's Security ID, when known
    segment: Mapped[str | None]               # costs.py segment: options, futures, equity_intraday, equity_delivery
    tag: Mapped[str | None]                   # free label, e.g. the strategy or signal that sent it
    side: Mapped[str]
    order_type: Mapped[str] = mapped_column(server_default="MARKET")
    product: Mapped[str] = mapped_column(server_default="INTRADAY")
    qty: Mapped[int]
    price: Mapped[Decimal | None]
    trigger_price: Mapped[Decimal | None]
    status: Mapped[str] = mapped_column(server_default="PENDING", index=True)
    broker_order_id: Mapped[str | None]
    filled_qty: Mapped[int] = mapped_column(server_default=text("0"))
    avg_fill_price: Mapped[Decimal | None]
    costs: Mapped[Decimal | None] = mapped_column(Money)     # brokerage + taxes of the fill (costs.py)
    filled_at: Mapped[dt.datetime | None]
    error_message: Mapped[str | None] = mapped_column(Text)
    placed_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        one_of("mode", ["paper", "live"]),
        one_of("side", ["BUY", "SELL"]),
        one_of("order_type", ["MARKET", "LIMIT", "SL", "SL-M"]),
        one_of("product", ["INTRADAY", "MARGIN", "CNC"]),
        one_of("status", ["PENDING", "PLACED", "PARTIAL", "FILLED", "REJECTED", "CANCELLED"]),
    )


class Position(Base):
    """An open or closed position. qty below 0 means a short (sold) position."""
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(server_default="paper")
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("option_contracts.id"))
    symbol: Mapped[str | None]
    security_id: Mapped[str | None]
    segment: Mapped[str | None]
    product: Mapped[str] = mapped_column(server_default="INTRADAY")
    qty: Mapped[int]
    avg_price: Mapped[Decimal]
    ltp: Mapped[Decimal | None]
    realized_pnl: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))   # before costs
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(Money)
    costs: Mapped[Decimal] = mapped_column(Money, server_default=text("0"))           # all orders so far
    is_open: Mapped[bool] = mapped_column(server_default=text("true"))
    opened_at: Mapped[dt.datetime] = created_now()
    closed_at: Mapped[dt.datetime | None]
    __table_args__ = (
        Index("ix_positions_open_mode", "is_open", "mode"),
        one_of("mode", ["paper", "live"]),
    )


# =====================================================================
# Alerts, journal, webhooks, broker login and errors
# =====================================================================

class PriceAlert(Base):
    """'Tell me on Telegram when NIFTY goes above 25,500.'"""
    __tablename__ = "price_alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    condition: Mapped[str]                    # ABOVE or BELOW
    target_price: Mapped[Decimal]
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))
    note: Mapped[str | None]
    created_at: Mapped[dt.datetime] = created_now()
    triggered_at: Mapped[dt.datetime | None]
    __table_args__ = (one_of("condition", ["ABOVE", "BELOW"]),)


class Notification(Base):
    """Every message sent (or waiting to be sent) to Telegram or shown in the app."""
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel: Mapped[str] = mapped_column(server_default="telegram")
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = created_now()
    sent_at: Mapped[dt.datetime | None]
    __table_args__ = (
        Index("ix_notifications_status_created", "status", "created_at"),
        one_of("channel", ["telegram", "app"]),
        one_of("status", ["pending", "sent", "failed"]),
    )


class TradeJournal(Base):
    """Your own notes about a trade: what happened, tags, screenshot and mistakes."""
    __tablename__ = "trade_journal"
    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[dt.date] = mapped_column(index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="SET NULL"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    title: Mapped[str]
    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(server_default=EMPTY_LIST)
    screenshot_path: Mapped[str | None]       # path to an image file, not the image itself
    mistakes: Mapped[str | None] = mapped_column(Text)
    pnl: Mapped[Decimal | None] = mapped_column(Money)
    created_at: Mapped[dt.datetime] = created_now()
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class WebhookEvent(Base):
    """Raw alert received from TradingView or Chartink (module 7)."""
    __tablename__ = "webhook_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str]
    received_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), index=True)
    payload: Mapped[dict] = mapped_column(server_default=EMPTY_JSON)
    processed: Mapped[bool] = mapped_column(server_default=text("false"))
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (one_of("source", ["tradingview", "chartink", "other"]),)


class BrokerSession(Base):
    """One row per day: did the Dhan login work and when does the token expire.

    The token itself is NEVER stored here; it stays in .env.
    """
    __tablename__ = "broker_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    broker: Mapped[str] = mapped_column(server_default="dhan")
    session_date: Mapped[dt.date]
    status: Mapped[str]
    token_expiry: Mapped[dt.datetime | None]
    logged_in_at: Mapped[dt.datetime | None]
    message: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("broker", "session_date"),
        one_of("broker", ["dhan"]),
        one_of("status", ["active", "expired", "failed"]),
    )


class DownloadProgress(Base):
    """Which days a long download has finished, so it can skip them and resume safely.

    dataset: e.g. "expired_options" or "index_minutes"
    key:     what was downloaded, e.g. "DHAN|NIFTY|WEEK|ATM+1|CE"
    rows:    how many rows that day gave (0 = holiday / no data, still counts as done)
    """
    __tablename__ = "download_progress"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dataset: Mapped[str]
    key: Mapped[str]
    day: Mapped[dt.date]
    rows: Mapped[int] = mapped_column(server_default=text("0"))
    done_at: Mapped[dt.datetime] = created_now()
    __table_args__ = (UniqueConstraint("dataset", "key", "day"),)


class ErrorLog(Base):
    """Errors from any module, so the Bot Monitor (module 11) can show them."""
    __tablename__ = "error_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(server_default=func.now(), index=True)
    level: Mapped[str] = mapped_column(server_default="ERROR")
    module: Mapped[str]
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict | None]
