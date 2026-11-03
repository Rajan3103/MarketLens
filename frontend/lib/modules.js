// The menu and the Home page cards come from this one list.
// web:       "ready" when the page is built in this web app, "soon" while it shows <ComingSoon>.
// streamlit: how far the same module is in the Streamlit app (streamlit run app.py): ready, partial or planned.
// planned:   what the web page will do (shown by <ComingSoon>).

export const START_PAGES = [
  { title: "Home", href: "/", icon: "🏠" },
  { title: "Settings & Status", href: "/settings", icon: "⚙️" },
];

export const GROUPS = [
  {
    name: "Market & data",
    modules: [
      {
        num: 1, title: "Live Market Overview", href: "/market-overview", icon: "📊", web: "ready", streamlit: "ready",
        description: "Index cards, Nifty 50 gainers and losers, breadth, sector heatmap and a price chart.",
        planned: ["Nifty, Bank Nifty, Fin Nifty, Sensex and India VIX cards",
          "Nifty 50 top gainers / losers and advance-decline breadth, refreshing in market hours",
          "Sector heatmap", "Price chart with 20 / 50-day averages and crossover signals"],
      },
      {
        num: 2, title: "Option Chain & Strategy Builder", href: "/option-chain", icon: "🔗", web: "soon", streamlit: "ready",
        description: "Option chain with PCR, max pain and OI; build multi-leg strategies with payoff and Greeks.",
        planned: ["Live option chain with PCR, max pain, ATM straddle and expected move", "OI and change-in-OI charts",
          "Strategy builder with 12 templates, payoff today and at expiry, net Greeks, probability of profit",
          "Save, backtest or paper trade a strategy"],
      },
      {
        num: 3, title: "Options Data Downloader", href: "/options-downloader", icon: "📥", web: "soon", streamlit: "ready",
        description: "Download option-chain snapshots, expired options and price history into PostgreSQL.",
        planned: ["Expired options, 1-minute, ATM ± 10 strikes", "Stocks, ETFs and indices daily prices",
          "Dhan intraday candles", "What is stored, with resume for long downloads"],
      },
    ],
  },
  {
    name: "Options testing",
    modules: [
      {
        num: 4, title: "Options Backtester", href: "/options-backtester", icon: "🧪", web: "soon", streamlit: "ready",
        description: "Backtest multi-leg option strategies minute by minute on stored data.",
        planned: ["Strikes by ATM ± N, premium or delta; SL, target, trailing, re-entry per leg",
          "Range breakout entry and overall M2M rules", "Equity curve, drawdown, day / month heatmaps",
          "Trade list with exit reasons, Excel report"],
      },
      {
        num: 5, title: "Option Simulator", href: "/option-simulator", icon: "⏪", web: "soon", streamlit: "ready",
        description: "Replay a past day minute by minute and practise trading it.",
        planned: ["Play, pause, step and speed 1x-30x", "The option chain as it was at that minute",
          "Virtual orders with SL / target, live P&L and payoff", "Saved practice sessions"],
      },
      {
        num: 6, title: "Forward Test / Paper Trading", href: "/forward-test", icon: "📝", web: "soon", streamlit: "ready",
        description: "Run a saved strategy on today's market with virtual money.",
        planned: ["Start / stop the background forward test", "Status, open legs and today's trades",
          "Day-by-day results next to the backtest"],
      },
    ],
  },
  {
    name: "Indicator & portfolio",
    modules: [
      {
        num: 7, title: "Signals & Indicator Strategy Builder", href: "/signals-builder", icon: "📐", web: "soon",
        streamlit: "ready",
        description: "Indicator rules without code, backtest, scanner, Pine Script export and webhook alerts.",
        planned: ["Conditions on SMA, EMA, RSI, MACD, Bollinger, Supertrend, VWAP, ATR (AND / OR)",
          "Backtest with entry / exit markers", "Scanner on Nifty 50 / 200, ETFs or a watchlist",
          "TradingView Pine Script v6 export and webhook alerts"],
      },
      {
        num: 8, title: "Momentum Engine Backtest", href: "/momentum-engine", icon: "🚀", web: "soon", streamlit: "partial",
        description: "A momentum stock portfolio against Nifty 50, after costs.",
        planned: ["Momentum scores with trend, liquidity and index filters", "Top-N portfolio, monthly or weekly rebalance",
          "CAGR, drawdown, Sharpe and Sortino against Nifty 50"],
      },
      {
        num: 9, title: "ETF Momentum Portfolio", href: "/etf-momentum", icon: "🧺", web: "soon", streamlit: "ready",
        description: "Top-K ETF rotation after costs and capital gains tax, against Nifty 50.",
        planned: ["Pick ETFs, top K, lookbacks and rebalance frequency", "Costs and Indian capital gains tax",
          "Check a claimed return; parameter sweep", "The current signal after each month end"],
      },
    ],
  },
  {
    name: "Control centre",
    modules: [
      {
        num: 10, title: "Personal Analytics & Strategy Library", href: "/analytics-library", icon: "📚", web: "soon",
        streamlit: "ready",
        description: "Every strategy, holding, trade and journal note in one place.",
        planned: ["Strategy library: open, duplicate, rename, delete, export / import",
          "Holdings and P&L with allocation by sector", "Trade journal with tags, rating and screenshots",
          "Combined performance against Nifty 50"],
      },
      {
        num: 11, title: "Live Algo Trading & Bot Monitor", href: "/live-trading", icon: "🤖", web: "soon",
        streamlit: "partial",
        description: "Deploy strategies, watch them run, and stop everything when needed.",
        planned: ["Deploy with lots multiplier, max loss and confirm / automatic orders", "Start, pause and stop per strategy",
          "Status cards, open positions, today's orders and confirmations", "Loss limits, kill switch and error log"],
      },
      {
        num: 12, title: "Telegram Bot Control Panel", href: "/telegram-panel", icon: "💬", web: "soon", streamlit: "planned",
        description: "Alerts and bot commands on Telegram.",
        planned: ["Alerts for entries, exits, stop losses, daily P&L and errors",
          "Commands: status, positions, P&L, stop a strategy, exit all", "Confirm / Reject buttons for live orders",
          "Price alerts"],
      },
    ],
  },
];

// Every module in one flat list.
export const MODULES = GROUPS.flatMap((group) => group.modules);

// The module whose page lives at this address (or undefined).
export function moduleFor(href) {
  return MODULES.find((m) => m.href === href);
}

// Words and colours for the two status fields.
export const WEB_STATUS = { ready: "Ready", soon: "Coming soon" };
export const STREAMLIT_STATUS = { ready: "Ready in the Streamlit app", partial: "Partly ready in the Streamlit app",
  planned: "Planned" };
