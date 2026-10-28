"""Payoff at expiry for any mix of option legs (the Strategy builder in module 2).

A leg is a dict: {"side": "BUY"/"SELL", "option_type": "CE"/"PE", "strike": 25000,
                  "premium": 150.0, "lots": 1, "lot_size": 65}

    prices = price_range(spot)                 # expiry prices to draw
    pnl = strategy_payoff(legs, prices)        # rupees at each price
    summary = payoff_summary(legs, prices, pnl)  # max profit, max loss, breakevens
"""
import numpy as np


# Expiry prices from spot - pct% to spot + pct%.
def price_range(spot: float, pct: float = 10.0, points: int = 801) -> np.ndarray:
    return np.linspace(spot * (1 - pct / 100), spot * (1 + pct / 100), points)


# Rupees one leg makes or loses at each expiry price (premium included).
def leg_payoff(leg: dict, prices: np.ndarray) -> np.ndarray:
    """Buyer: intrinsic value - premium paid. Seller: premium received - intrinsic value."""
    strike = float(leg["strike"])
    if leg["option_type"].upper() == "CE":
        intrinsic = np.maximum(prices - strike, 0)
    else:
        intrinsic = np.maximum(strike - prices, 0)
    sign = 1 if leg["side"].upper() == "BUY" else -1
    return sign * (intrinsic - float(leg["premium"])) * int(leg["lots"]) * int(leg["lot_size"])


# Total rupees of all legs together at each expiry price.
def strategy_payoff(legs: list[dict], prices: np.ndarray) -> np.ndarray:
    total = np.zeros(len(prices))
    for leg in legs:
        total += leg_payoff(leg, prices)
    return total


# Expiry prices where the profit/loss crosses zero (found between the drawn points).
def breakevens(prices: np.ndarray, pnl: np.ndarray) -> list[float]:
    points = []
    for i in range(len(prices) - 1):
        a, b = pnl[i], pnl[i + 1]
        if a == 0:
            points.append(float(prices[i]))
        elif a * b < 0:   # sign changes between two points: straight-line in between
            points.append(float(prices[i] - a * (prices[i + 1] - prices[i]) / (b - a)))
    if len(pnl) and pnl[-1] == 0:
        points.append(float(prices[-1]))
    return sorted(set(round(p, 2) for p in points))


# Units of calls bought minus units sold: above 0 = profit grows forever on a rally, below 0 = loss does.
def net_call_units(legs: list[dict]) -> int:
    return sum((1 if leg["side"].upper() == "BUY" else -1) * int(leg["lots"]) * int(leg["lot_size"])
               for leg in legs if leg["option_type"].upper() == "CE")


# Max profit, max loss, breakevens and net premium of a strategy.
def payoff_summary(legs: list[dict], prices: np.ndarray, pnl: np.ndarray | None = None) -> dict:
    """max_profit / max_loss are None when unlimited (open calls on a rally).
    Below the drawn range the worst case is at a price of 0, which is checked too."""
    pnl = strategy_payoff(legs, prices) if pnl is None else pnl
    # A payoff only bends at strikes, so its exact highs and lows are at a strike or at price 0
    corners = strategy_payoff(legs, np.array([0.0] + [float(leg["strike"]) for leg in legs]))
    calls = net_call_units(legs)
    best, worst = max(pnl.max(), corners.max()), min(pnl.min(), corners.min())
    premium = sum((-1 if leg["side"].upper() == "BUY" else 1) * float(leg["premium"])
                  * int(leg["lots"]) * int(leg["lot_size"]) for leg in legs)
    return {"max_profit": None if calls > 0 else float(best),
            "max_loss": None if calls < 0 else float(worst),
            "breakevens": breakevens(prices, pnl),
            "net_premium": float(premium)}   # + received (credit), - paid (debit)
