"""Tests for marketlens/broker/paper.py (and the Broker interface in base.py).

Most tests use save=False (no database). The last ones save to TEST_DATABASE_URL and check that
orders and positions are stored with mode='paper' and loaded again by a new PaperBroker.
"""
import pytest

from marketlens.broker.base import Broker, OrderResult, check_order
from marketlens.broker.paper import PaperBroker, segment_for
from marketlens.costs import CostSettings, apply_slippage, trade_costs

NO_COSTS = CostSettings(brokerage_per_order=0, include_taxes=False)
OPTION = "NIFTY 25000 CE 03-Nov-2026"


# A paper broker with Rs 10 lakh, no database.
def broker(costs=NO_COSTS, **kw) -> PaperBroker:
    return PaperBroker(capital=1_000_000, costs=costs, save=False, **kw)


# ---- the interface ----

def test_paper_is_a_broker():
    assert isinstance(broker(), Broker) and PaperBroker.mode == "paper"
    with pytest.raises(TypeError):
        Broker()                                              # abstract: cannot be used directly
    assert check_order("buy", 1, "limit", 10, "cnc") == ("BUY", "LIMIT", "CNC")
    for bad in [("HOLD", 1, "MARKET", None, "CNC"), ("BUY", 0, "MARKET", None, "CNC"), ("BUY", 1.5, "MARKET", None, "CNC"),
                ("BUY", 1, "LIMIT", None, "CNC"), ("BUY", 1, "MARKET", None, "DELIVERY"), ("BUY", 1, "ICEBERG", None, "CNC")]:
        with pytest.raises(ValueError):
            check_order(*bad)
    with pytest.raises(ValueError):
        check_order("BUY", 1, "SL-M", None, "INTRADAY")           # needs a trigger price


def test_segments():
    assert segment_for(OPTION) == "options" and segment_for("NIFTY25NOV25000PE") == "options"
    assert segment_for("NIFTY-Nov2026-FUT") == "futures"
    assert segment_for("RELIANCE", "CNC") == "equity_delivery" and segment_for("RELIANCE") == "equity_intraday"


# ---- buy then sell: realised P&L ----

# Buy 100 at 2,500, sell 100 at 2,600: +10,000 before costs; costs are costs.py's buy + sell.
def test_buy_then_sell_realised_pnl():
    b = broker(costs=CostSettings())
    b.update_price("RELIANCE", 2500)
    buy = b.place_order("RELIANCE", "BUY", 100, product="CNC")
    assert buy.filled and buy.avg_price == 2500
    b.update_price("RELIANCE", 2600)
    sell = b.place_order("RELIANCE", "SELL", 100, product="CNC")
    costs = (trade_costs("equity_delivery", "BUY", 2500, 100).total
             + trade_costs("equity_delivery", "SELL", 2600, 100).total)
    assert buy.costs + sell.costs == pytest.approx(costs)
    funds = b.get_funds()
    assert funds["realized_pnl"] == 10_000 and funds["costs"] == pytest.approx(costs)
    assert funds["net_pnl"] == pytest.approx(10_000 - costs) and funds["used"] == 0
    assert funds["available"] == pytest.approx(1_000_000 + 10_000 - costs)
    row = b.get_positions().iloc[0]
    assert (row.qty, row.realized_pnl, bool(row.is_open)) == (0, 10_000, False)


# Sell an option first, buy it back cheaper: +(120 - 100) x 65 = 1,300.
def test_short_then_cover():
    b = broker()
    b.update_price(OPTION, 120)
    b.place_order(OPTION, "SELL", 65)
    assert b.get_positions().iloc[0].qty == -65
    b.update_price(OPTION, 100)
    b.place_order(OPTION, "BUY", 65)
    assert b.get_funds()["realized_pnl"] == 1300


# Buy 10 at 100, buy 10 at 110 -> average 105; sell 4 at 120 -> +60; sell 20 at 90 -> 16 x -15 = -240,
# and the extra 4 open a short at 90.
def test_average_partial_close_and_flip():
    b = broker()
    for price in (100, 110):
        b.update_price("SBIN", price)
        b.place_order("SBIN", "BUY", 10)
    pos = b.positions[("SBIN", "INTRADAY")]
    assert (pos.qty, pos.avg_price) == (20, 105)
    b.update_price("SBIN", 120)
    b.place_order("SBIN", "SELL", 4)
    assert (pos.qty, pos.realized) == (16, 60)
    b.update_price("SBIN", 90)
    b.place_order("SBIN", "SELL", 20)
    assert (pos.qty, pos.avg_price, pos.realized) == (-4, 90, 60 - 240)
    assert pos.unrealized == 0
    b.update_price("SBIN", 80)
    assert pos.unrealized == 40 and b.get_funds()["unrealized_pnl"] == 40


# Market orders get the slippage: 0.1% -> buy 2,502.50, sell 2,497.50.
def test_market_slippage():
    b = broker(costs=CostSettings(slippage_type="pct", slippage_value=0.1, brokerage_per_order=0, include_taxes=False))
    b.update_price("TCS", 2500)
    assert b.place_order("TCS", "BUY", 1).avg_price == 2502.5
    assert b.place_order("TCS", "SELL", 1).avg_price == 2497.5
    assert b.get_funds()["realized_pnl"] == -5
    assert apply_slippage(10, "BUY", CostSettings(slippage_type="points", slippage_value=1)) == 11


# ---- limit and stop orders ----

# Buy limit at 98: waits at 101 and 99, fills when the price reaches 98 (at 98, no slippage).
def test_limit_fills_when_price_crosses():
    b = broker(costs=CostSettings(slippage_value=1))
    b.update_price("INFY", 101)
    order = b.place_order("INFY", "BUY", 5, "LIMIT", price=98)
    assert order.status == "PLACED" and b.get_positions().empty
    assert b.update_price("INFY", 99) == []
    filled = b.update_price("INFY", 98)
    assert filled[0].status == "FILLED" and filled[0].avg_price == 98
    # A sell limit below the price fills at once, at the better market price
    sell = b.place_order("INFY", "SELL", 5, "LIMIT", price=95)
    assert sell.filled and sell.avg_price == 98
    # A gap through the limit fills at the better price
    b.place_order("INFY", "BUY", 1, "LIMIT", price=90)
    assert b.update_price("INFY", 85)[0].avg_price == 85


# Stop-market sell at 95 triggers when the price falls to 95 and fills like a market order.
def test_stop_orders():
    b = broker()
    b.update_price("HDFCBANK", 100)
    b.place_order("HDFCBANK", "BUY", 10)
    stop = b.place_order("HDFCBANK", "SELL", 10, "SL-M", trigger_price=95)
    assert stop.status == "PLACED" and b.update_price("HDFCBANK", 96) == []
    assert b.update_price("HDFCBANK", 94)[0].avg_price == 94
    # Stop-limit: trigger 105, limit 106 -> becomes a buy limit at 106 once 105 trades
    sl = b.place_order("HDFCBANK", "BUY", 1, "SL", price=106, trigger_price=105)
    assert b.update_price("HDFCBANK", 104) == []
    assert b.update_price("HDFCBANK", 105.5)[0].avg_price == 105.5
    assert sl.order_id is not None


# Cancel a waiting order; a filled one cannot be cancelled.
def test_cancel():
    b = broker()
    b.update_price("ITC", 400)
    waiting = b.place_order("ITC", "BUY", 1, "LIMIT", price=390)
    assert b.cancel_order(waiting.order_id).status == "CANCELLED"
    assert b.update_price("ITC", 380) == []                          # cancelled: does not fill
    done = b.place_order("ITC", "BUY", 1)
    again = b.cancel_order(done.order_id)
    assert again.status == "FILLED" and "cannot be cancelled" in again.message
    assert b.cancel_order(999).status == "REJECTED"
    assert list(b.get_orders()["status"]) == ["FILLED", "CANCELLED"]


# ---- risk checks ----

def test_rejections():
    b = broker(max_open_positions=2)
    assert b.place_order("LT", "BUY", 1).status == "REJECTED"            # no price yet
    for sym in ("A", "B", "C"):
        b.update_price(sym, 100)
    b.place_order("A", "BUY", 1)
    b.place_order("B", "BUY", 1)
    third = b.place_order("C", "BUY", 1)
    assert third.status == "REJECTED" and "MAX_OPEN_POSITIONS" in third.message
    assert b.place_order("A", "SELL", 1).filled                           # closing is always allowed
    big = b.place_order("C", "BUY", 20_000)                               # Rs 20 lakh > Rs 10 lakh
    assert big.status == "REJECTED" and "Not enough virtual funds" in big.message


# A price feed can be given instead of update_price.
def test_price_feed():
    b = broker(prices=lambda symbol, security_id: {"WIPRO": 250.0}.get(symbol))
    assert b.place_order("WIPRO", "BUY", 4).avg_price == 250
    assert b.place_order("UNKNOWN", "BUY", 1).status == "REJECTED"


# ---- saved in orders / positions with mode='paper' (test database only) ----

def test_saved_and_loaded_again(db_url, db_session):
    from sqlalchemy import select

    from marketlens.models import Order, Position
    b = PaperBroker(capital=1_000_000, costs=CostSettings(), url=db_url)
    b.update_price("RELIANCE", 2500)
    b.place_order("RELIANCE", "BUY", 100, product="CNC", tag="test", security_id="2885")
    pending = b.place_order("RELIANCE", "SELL", 100, "LIMIT", price=2600, product="CNC")
    orders = db_session.scalars(select(Order).order_by(Order.id)).all()
    assert [(o.mode, o.status, o.symbol) for o in orders] == [("paper", "FILLED", "RELIANCE"), ("paper", "PLACED", "RELIANCE")]
    assert orders[0].tag == "test" and orders[0].security_id == "2885" and float(orders[0].costs) > 0

    # A new broker (e.g. after restarting the app) continues: open position and waiting order are back
    again = PaperBroker(capital=1_000_000, costs=CostSettings(), url=db_url)
    assert again.positions[("RELIANCE", "CNC")].qty == 100
    filled = again.update_price("RELIANCE", 2601)
    assert filled[0].order_id == pending.order_id and filled[0].avg_price == 2601
    db_session.expire_all()
    pos = db_session.scalars(select(Position)).one()
    assert (pos.mode, pos.qty, float(pos.realized_pnl), pos.is_open) == ("paper", 0, 10_100.0, False)
    assert again.get_orders()["status"].tolist() == ["FILLED", "FILLED"]
    assert again.get_positions().iloc[0]["realized_pnl"] == 10_100

    # A third broker sees the closed P&L in its funds
    third = PaperBroker(capital=1_000_000, costs=CostSettings(), url=db_url)
    funds = third.get_funds()
    assert funds["realized_pnl"] == 10_100 and funds["costs"] == pytest.approx(float(pos.costs))


def test_rejected_order_is_saved(db_url, db_session):
    from sqlalchemy import select

    from marketlens.models import Order
    PaperBroker(url=db_url).place_order("NOPRICE", "BUY", 1)
    row = db_session.scalars(select(Order)).one()
    assert (row.mode, row.status) == ("paper", "REJECTED") and "No price" in row.error_message


def test_order_result():
    assert OrderResult(1, "FILLED").filled and not OrderResult(1, "PLACED").filled
