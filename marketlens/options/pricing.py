"""Black-Scholes option prices, implied volatility and Greeks (European options, like NSE index options).

Arguments everywhere (same order in every function):
    S      spot price of the underlying            e.g. 25000
    K      strike price                            e.g. 25000
    T      time to expiry in YEARS                 e.g. 7 days = 7 / 365
    r      risk-free rate as a fraction            use RISK_FREE_RATE (6.5%)
    sigma  volatility as a fraction                e.g. 0.14 for 14% (India VIX / 100)
    option_type  "CE" (call) or "PE" (put)

    price = bs_price(25000, 25000, 7/365, RISK_FREE_RATE, 0.14, "CE")
    iv    = implied_vol(price, 25000, 25000, 7/365, RISK_FREE_RATE, "CE")   -> 0.14
    g     = greeks(25000, 25000, 7/365, RISK_FREE_RATE, 0.14, "CE")        -> delta, gamma, theta, vega

Used by the DEMO data client now, and later by the option chain, strategy builder,
backtester and simulator.
"""
import math

# India's risk-free rate (roughly the 91-day T-bill yield, 6.5%); check rbi.org.in now and then
RISK_FREE_RATE = 0.065
MIN_T = 1 / (365 * 24 * 60)          # one minute in years: avoids dividing by zero at expiry
IV_LOW, IV_HIGH = 1e-4, 5.0          # implied volatility is searched between 0.01% and 500%
IV_PRICE_TOLERANCE = 1e-6            # stop when the model price is within 0.000001 rupees
OPTION_TYPES = ("CE", "PE")


# The standard normal distribution: chance that a value is below x.
def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# The bell curve's height at x (used for gamma and vega).
def norm_pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


# Check the option type and return it in capitals.
def _check_type(option_type: str) -> str:
    option_type = option_type.strip().upper()
    if option_type not in OPTION_TYPES:
        raise ValueError("option_type must be CE (call) or PE (put)")
    return option_type


# d1 and d2: the two numbers every Black-Scholes formula needs.
def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    T = max(T, MIN_T)
    d1 = (math.log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * math.sqrt(T))
    return d1, d1 - sigma * math.sqrt(T)


# Fair price of a call (CE) or put (PE).
def bs_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> float:
    """At or after expiry (T <= 0) the option is worth only its intrinsic value."""
    option_type = _check_type(option_type)
    if S <= 0 or K <= 0:
        raise ValueError("S and K must be above 0")
    if T <= 0:
        return max(S - K, 0.0) if option_type == "CE" else max(K - S, 0.0)
    if sigma <= 0:
        raise ValueError("sigma must be above 0")
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    discount = math.exp(-r * T)
    if option_type == "CE":
        return S * norm_cdf(d1) - K * discount * norm_cdf(d2)
    return K * discount * norm_cdf(-d2) - S * norm_cdf(-d1)


# Delta, gamma, theta (per day) and vega (per 1% of IV), like Dhan's option chain.
def greeks(S: float, K: float, T: float, r: float, sigma: float, option_type: str) -> dict:
    """delta: price change per Rs 1 move in spot     gamma: delta change per Rs 1 move
    theta: price change per calendar day (negative: options lose value with time)
    vega:  price change per 1 percentage point of IV (e.g. 14% -> 15%)"""
    option_type = _check_type(option_type)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    T = max(T, MIN_T)
    discount = math.exp(-r * T)
    gamma = norm_pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * norm_pdf(d1) * math.sqrt(T) / 100
    decay = -S * norm_pdf(d1) * sigma / (2 * math.sqrt(T))
    if option_type == "CE":
        delta = norm_cdf(d1)
        theta = (decay - r * K * discount * norm_cdf(d2)) / 365
    else:
        delta = norm_cdf(d1) - 1
        theta = (decay + r * K * discount * norm_cdf(-d2)) / 365
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


# The lowest and highest price an option can have (no-arbitrage limits).
def price_bounds(S: float, K: float, T: float, r: float, option_type: str) -> tuple[float, float]:
    """Call: between max(S - K x discount, 0) and S.  Put: between max(K x discount - S, 0) and K x discount."""
    discount = math.exp(-r * max(T, 0))
    if _check_type(option_type) == "CE":
        return max(S - K * discount, 0.0), S
    return max(K * discount - S, 0.0), K * discount


# The volatility that makes Black-Scholes give this market price (NaN when impossible).
def implied_vol(price: float, S: float, K: float, T: float, r: float, option_type: str,
                max_steps: int = 100) -> float:
    """Safe method: Newton steps (fast) while they stay inside a known bracket, otherwise
    bisection (always works). Returns float('nan') when the price is outside the possible
    range (below intrinsic value or above the maximum), or when T <= 0."""
    option_type = _check_type(option_type)
    low_price, high_price = price_bounds(S, K, T, r, option_type)
    if T <= 0 or price is None or not (low_price < price < high_price):
        return float("nan")

    low, high = IV_LOW, IV_HIGH              # the answer is always between these two
    # Starting guess (Brenner-Subrahmanyam): good for near-the-money options
    sigma = min(max(math.sqrt(2 * math.pi / T) * price / S, 0.05), high)
    for _ in range(max_steps):
        diff = bs_price(S, K, T, r, sigma, option_type) - price
        if abs(diff) < IV_PRICE_TOLERANCE:
            return sigma
        # Price rises with volatility, so the answer is below sigma if diff > 0, above if diff < 0
        if diff > 0:
            high = sigma
        else:
            low = sigma
        vega = greeks(S, K, T, r, sigma, option_type)["vega"] * 100   # per 1.00 of sigma
        step = sigma - diff / vega if vega > 1e-12 else None
        # Use the Newton step only if it stays inside the bracket; otherwise go halfway
        sigma = step if step is not None and low < step < high else (low + high) / 2
        if high - low < 1e-10:
            return sigma
    return sigma
