"""TradingView Pine Script (v6) from MarketLens rules, plus a validator.

    code = rules_to_pine(strategy)            # the same rules as the backtester, as Pine Script v6
    problems = validate_pine(code)            # [] when it looks valid

The script has:
  - //@version=6 and strategy() with Indian costs (approximate commission % from costs.py, slippage);
  - input.* for every setting (indicator lengths, numbers, SL / target %, quantity, webhook secret);
  - ta.* functions, the entry / exit rules, SL / target with strategy.exit;
  - alert() calls that send JSON in the MarketLens webhook format (see marketlens/webhook/payload.py),
    with a secret placeholder you replace in TradingView's settings, never in this code.
"""
import re

from marketlens.costs import CostSettings, round_trip_costs
from marketlens.signals.rules import Condition, Operand, SignalStrategy

SECRET_PLACEHOLDER = "PASTE_YOUR_WEBHOOK_SECRET"
TICK = 0.05                                       # NSE tick size for most stocks


# ---------------------------------------------------------------- the old template

# Moving-average crossover (the first Pine template of this page), kept for quick use.
def ma_crossover_script(fast: int = 20, slow: int = 50) -> str:
    return f"""//@version=6
// MarketLens: moving-average crossover (buy when the fast average crosses above the slow one)
strategy("MarketLens MA Crossover {fast}/{slow}", overlay=true)

fastLen = input.int({fast}, "Fast moving average", minval=1)
slowLen = input.int({slow}, "Slow moving average", minval=2)
fastMA = ta.sma(close, fastLen)
slowMA = ta.sma(close, slowLen)
plot(fastMA, "Fast MA", color.green)
plot(slowMA, "Slow MA", color.red)

// Buy when the fast average crosses above the slow one
if ta.crossover(fastMA, slowMA)
    strategy.entry("Long", strategy.long)

// Exit when it crosses back below
if ta.crossunder(fastMA, slowMA)
    strategy.close("Long")
"""


# ---------------------------------------------------------------- rules -> Pine

# Approximate Indian costs per order as a percent of the trade value, worked out with costs.py.
def commission_pct(intraday: bool, costs: CostSettings | None = None) -> float:
    costs = CostSettings(**{**(costs or CostSettings()).__dict__, "slippage_value": 0.0})
    segment = "equity_intraday" if intraday else "equity_delivery"
    value = 100 * 1000.0                          # a typical trade: 100 shares at Rs 1,000
    return round(round_trip_costs(segment, 1000, 1000, 100, costs) / (2 * value) * 100, 4)


# Slippage in ticks (TradingView only accepts ticks); a % slippage is shown as a comment instead.
def slippage_ticks(costs: CostSettings) -> int:
    return int(round(costs.slippage_value / TICK)) if costs.slippage_type == "points" else 0


class _Builder:
    """Gives every indicator one Pine variable and one input per setting."""

    def __init__(self):
        self.inputs: list[str] = []
        self.calcs: list[str] = []
        self.names: dict = {}
        self.plots: list[str] = []
        self.count: dict = {}

    # A new numbered name like ema1, ema2.
    def _name(self, base: str) -> str:
        self.count[base] = self.count.get(base, 0) + 1
        return f"{base}{self.count[base]}"

    # The Pine expression for one operand (declaring its inputs and calculation the first time).
    def expr(self, op: Operand) -> str:
        k = op.kind
        if k in ("close", "open", "high", "low", "volume"):
            return k
        if k == "number":
            key = ("number", op.value)
            if key not in self.names:
                name = self._name("level")
                self.inputs.append(f'{name} = input.float({op.value:g}, "Level {op.value:g}")')
                self.names[key] = name
            return self.names[key]
        family = "macd" if k.startswith("macd") else "bb" if k.startswith("bb_") else k
        key = (family, tuple(op.used().items()))
        if key not in self.names:
            self.names[key] = self._declare(family, op)
        base = self.names[key]
        return {"macd_signal": base + "Signal", "macd_hist": base + "Hist",
                "bb_upper": base + "Upper", "bb_mid": base + "Mid", "bb_lower": base + "Lower"}.get(k, base)

    # Inputs + calculation for one indicator family.
    def _declare(self, family: str, op: Operand) -> str:
        n = self._name({"supertrend": "st"}.get(family, family))
        label = n.upper()
        if family in ("sma", "ema", "rsi", "atr"):
            self.inputs.append(f'{n}Len = input.int({op.length}, "{label} length", minval=1)')
            call = {"sma": f"ta.sma(close, {n}Len)", "ema": f"ta.ema(close, {n}Len)",
                    "rsi": f"ta.rsi(close, {n}Len)", "atr": f"ta.atr({n}Len)"}[family]
            self.calcs.append(f"{n} = {call}")
            if family in ("sma", "ema"):
                self.plots.append(f'plot({n}, "{label}")')
        elif family == "vwap":
            self.calcs.append(f"{n} = ta.vwap(hlc3)")
            self.plots.append(f'plot({n}, "VWAP")')
        elif family == "supertrend":
            self.inputs.append(f'{n}Len = input.int({op.length}, "Supertrend ATR length", minval=1)')
            self.inputs.append(f'{n}Mult = input.float({op.mult:g}, "Supertrend multiplier", minval=0.1)')
            self.calcs.append(f"[{n}, {n}Dir] = ta.supertrend({n}Mult, {n}Len)")
            self.plots.append(f'plot({n}, "Supertrend")')
        elif family == "macd":
            self.inputs.append(f'{n}Fast = input.int({op.fast}, "MACD fast", minval=1)')
            self.inputs.append(f'{n}Slow = input.int({op.slow}, "MACD slow", minval=2)')
            self.inputs.append(f'{n}Sig = input.int({op.signal}, "MACD signal", minval=1)')
            self.calcs.append(f"[{n}, {n}Signal, {n}Hist] = ta.macd(close, {n}Fast, {n}Slow, {n}Sig)")
        else:                                                         # Bollinger
            self.inputs.append(f'{n}Len = input.int({op.length}, "Bollinger length", minval=1)')
            self.inputs.append(f'{n}Mult = input.float({op.mult:g}, "Bollinger multiplier", minval=0.1)')
            self.calcs.append(f"[{n}Mid, {n}Upper, {n}Lower] = ta.bb(close, {n}Len, {n}Mult)")
            self.plots += [f'plot({n}Upper, "BB upper")', f'plot({n}Mid, "BB middle")', f'plot({n}Lower, "BB lower")']
        return n

    # One condition as a Pine boolean expression.
    def condition(self, c: Condition) -> str:
        a, b = self.expr(c.left), self.expr(c.right)
        if c.op == "crosses_above":
            return f"ta.crossover({a}, {b})"
        if c.op == "crosses_below":
            return f"ta.crossunder({a}, {b})"
        return f"{a} {c.op} {b}"


# Text that is safe inside a Pine string (no quotes or backslashes).
def _safe(text: str) -> str:
    return re.sub(r"[\"'\\\n]", " ", text).strip()


# The strategy's rules as a complete Pine Script v6 strategy with MarketLens webhook alerts.
def rules_to_pine(s: SignalStrategy) -> str:
    b = _Builder()
    entry = f" {s.entry_join.lower()} ".join(f"({b.condition(c)})" for c in s.entry)
    exit_ = f" {s.exit_join.lower()} ".join(f"({b.condition(c)})" for c in s.exit) if s.exit else "false"
    long = s.side == "long"
    name = _safe(s.name) or "MarketLens strategy"
    if len(name) > 60:
        name = name[:57].rstrip() + "..."
    commission = commission_pct(s.intraday, s.costs)
    ticks = slippage_ticks(s.costs)
    entry_side = "BUY" if long else "SELL"
    direction = "strategy.long" if long else "strategy.short"
    sl_sign, tg_sign = ("-", "+") if long else ("+", "-")
    rules = "\n".join(f"//   entry: {_safe(c.label())}" for c in s.entry) + "\n" + \
        ("\n".join(f"//   exit:  {_safe(c.label())}" for c in s.exit) if s.exit else "//   exit:  SL / target only")
    lines = [
        "//@version=6",
        f"// MarketLens export: {name}",
        f"// Rules ({s.entry_join} for entry, {s.exit_join} for exit), {s.timeframe} candles, {s.side}:",
        rules,
        f"// Costs: about {commission}% per order (Indian brokerage, STT, exchange, SEBI, stamp duty, GST from",
        "// MarketLens costs.py)" + (f"; slippage {ticks} ticks." if ticks else
                                    f"; slippage {s.costs.slippage_value:g}% is not modelled here (TradingView needs ticks)."),
        "// Historical analysis for learning, not a live trading recommendation.",
        f'strategy("{name}", overlay=true, initial_capital={int(s.capital)}, currency=currency.INR,',
        f"         default_qty_type=strategy.fixed, default_qty_value={s.qty},",
        f"         commission_type=strategy.commission.percent, commission_value={commission}, slippage={ticks})",
        "",
        "// ---- settings (change them in the strategy's Settings > Inputs) ----",
        *b.inputs,
        f'qtyInput = input.int({s.qty}, "Quantity", minval=1)',
        f'slPct = input.float({s.sl_pct or 0:g}, "Stop loss % (0 = off)", minval=0)',
        f'tgtPct = input.float({s.target_pct or 0:g}, "Target % (0 = off)", minval=0)',
        f'webhookSecret = input.string("{SECRET_PLACEHOLDER}", "MarketLens webhook secret")',
        "",
        "// ---- indicators ----",
        *b.calcs,
        *b.plots,
        "",
        "// ---- rules ----",
        f"entrySignal = {entry}",
        f"exitSignal = {exit_}",
        "",
        "// The alert message in the MarketLens webhook format (JSON)",
        "mlJson(string action, string note) =>",
        "    '{\"secret\":\"' + webhookSecret + '\",\"strategy\":\"" + name + "\",'"
        " + '\"symbol\":\"' + syminfo.ticker + '\",\"action\":\"' + action + '\",\"qty\":' + str.tostring(qtyInput) + ','"
        " + '\"price\":' + str.tostring(close) + ',\"note\":\"' + note + '\",'"
        " + '\"exchange\":\"' + syminfo.prefix + '\",\"timeframe\":\"' + timeframe.period + '\",'"
        " + '\"time\":\"' + str.format_time(time_close, \"yyyy-MM-dd'T'HH:mm:ssZ\", \"Asia/Kolkata\") + '\"}'",
        "",
        "// Enter when the entry rule is true at a candle close (the order fills at the next open)",
        "if entrySignal and strategy.position_size == 0",
        f'    strategy.entry("ML", {direction}, qty=qtyInput)',
        f'    alert(mlJson("{entry_side}", "entry rule"), alert.freq_once_per_bar_close)',
        "",
        "// Exit when the exit rule is true",
        "if exitSignal and strategy.position_size != 0",
        '    strategy.close("ML", comment="exit rule")',
        '    alert(mlJson("EXIT", "exit rule"), alert.freq_once_per_bar_close)',
        "",
        "// Stop loss and target as % of the entry price",
        "if strategy.position_size != 0 and (slPct > 0 or tgtPct > 0)",
        f"    float stopPrice = slPct > 0 ? strategy.position_avg_price * (1 {sl_sign} slPct / 100) : na",
        f"    float limitPrice = tgtPct > 0 ? strategy.position_avg_price * (1 {tg_sign} tgtPct / 100) : na",
        '    strategy.exit("ML SL/TGT", "ML", stop=stopPrice, limit=limitPrice)',
    ]
    if s.intraday:
        lines += [
            "",
            "// Intraday: close everything at 15:15 India time",
            'if strategy.position_size != 0 and hour(time, "Asia/Kolkata") == 15 and minute(time, "Asia/Kolkata") >= 15',
            '    strategy.close_all(comment="day end")',
            '    alert(mlJson("EXIT", "day end"), alert.freq_once_per_bar_close)',
        ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- validator

PYTHON_LINE = re.compile(r"^\s*(def |import |from \S+ import|elif |class |lambda |print\(|return |pass$|try:|except)")
PYTHON_WORD = re.compile(r"\b(True|False|None|self\.)\b|\*\*")


# Remove string literals and // comments from one line (so brackets inside them are not counted).
def _code_only(line: str) -> tuple[str, bool]:
    out, quote, i = [], None, 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif line.startswith("//", i):
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out), quote is None


# Problems found in a Pine Script (an empty list means it passed every check).
def validate_pine(code: str) -> list[str]:
    problems = []
    lines = code.splitlines()
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    if first != "//@version=6":
        problems.append("The first line must be //@version=6")
    if not re.search(r"^\s*(strategy|indicator)\(", code, re.M):
        problems.append("No strategy(...) or indicator(...) declaration")
    pairs, stack = {")": "(", "]": "[", "}": "{"}, []
    for n, raw in enumerate(lines, start=1):
        if "\t" in raw:
            problems.append(f"Line {n}: use spaces, not tabs")
        if raw.lstrip().startswith("#"):
            problems.append(f"Line {n}: '#' comments are Python; Pine uses //")
        code_part, closed = _code_only(raw)
        if not closed:
            problems.append(f"Line {n}: a string is not closed")
        if PYTHON_LINE.search(code_part) or PYTHON_WORD.search(code_part):
            problems.append(f"Line {n}: looks like Python, not Pine: {raw.strip()[:60]}")
        if re.search(r"^\s*(if|for|while|else)\b.*:\s*$", code_part):
            problems.append(f"Line {n}: Pine blocks do not end with ':'")
        indent = len(raw) - len(raw.lstrip(" "))
        if not stack and raw.strip() and indent % 4:       # a wrapped line inside brackets may use any indent
            problems.append(f"Line {n}: indentation must be a multiple of 4 spaces")
        for ch in code_part:
            if ch in "([{":
                stack.append((ch, n))
            elif ch in ")]}":
                if not stack or stack[-1][0] != pairs[ch]:
                    problems.append(f"Line {n}: '{ch}' has no matching opening bracket")
                else:
                    stack.pop()
    problems += [f"Line {n}: '{ch}' is never closed" for ch, n in stack]
    return problems
