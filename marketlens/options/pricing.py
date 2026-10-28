"""Black-Scholes option prices and Greeks (European options, like NSE index options).

Used by the DEMO data client to make realistic option prices, and later by the
option chain, strategy builder and simulator.

    price = bs_price(spot=25000, strike=25000, years=7/365, vol=0.14, option_type="CE")
"""
import math

# India's risk-free rate (roughly the 91-day T-bill yield); check rbi.org.in now and then
RISK_FREE_RATE = 0.065
MIN_YEARS = 1 / (365 * 24 * 60)   # one minute: avoids dividing by zero at expiry


# The standard normal distribution: chance that a value is below x.
def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# The bell curve's height at x (used for gamma and vega).
def norm_pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


# d1 and d2: the two numbers every Black-Scholes formula needs.
def _d1_d2(spot: float, strike: float, years: float, vol: float, rate: float) -> tuple[float, float]:
    years = max(years, MIN_YEARS)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    return d1, d1 - vol * math.sqrt(years)


# Fair price of a call (CE) or put (PE).
def bs_price(spot: float, strike: float, years: float, vol: float, option_type: str,
             rate: float = RISK_FREE_RATE) -> float:
    """years: time to expiry in years (e.g. 7 days = 7/365); vol: 0.14 for 14% (India VIX / 100)."""
    if spot <= 0 or strike <= 0 or vol <= 0:
        raise ValueError("spot, strike and vol must be above 0")
    option_type = option_type.upper()
    if years <= 0:  # at expiry the option is worth only its intrinsic value
        return max(spot - strike, 0.0) if option_type == "CE" else max(strike - spot, 0.0)
    d1, d2 = _d1_d2(spot, strike, years, vol, rate)
    discount = math.exp(-rate * years)
    if option_type == "CE":
        return spot * norm_cdf(d1) - strike * discount * norm_cdf(d2)
    if option_type == "PE":
        return strike * discount * norm_cdf(-d2) - spot * norm_cdf(-d1)
    raise ValueError("option_type must be CE or PE")


# Delta, gamma, theta (per day) and vega (per 1% of volatility), like Dhan's option chain.
def bs_greeks(spot: float, strike: float, years: float, vol: float, option_type: str,
              rate: float = RISK_FREE_RATE) -> dict:
    """delta: price change per Rs 1 move in spot; gamma: delta change per Rs 1;
    theta: price lost per day; vega: price change per 1% change in volatility."""
    option_type = option_type.upper()
    d1, d2 = _d1_d2(spot, strike, years, vol, rate)
    years = max(years, MIN_YEARS)
    discount = math.exp(-rate * years)
    gamma = norm_pdf(d1) / (spot * vol * math.sqrt(years))
    vega = spot * norm_pdf(d1) * math.sqrt(years) / 100
    decay = -spot * norm_pdf(d1) * vol / (2 * math.sqrt(years))
    if option_type == "CE":
        delta = norm_cdf(d1)
        theta = (decay - rate * strike * discount * norm_cdf(d2)) / 365
    else:
        delta = norm_cdf(d1) - 1
        theta = (decay + rate * strike * discount * norm_cdf(-d2)) / 365
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}
