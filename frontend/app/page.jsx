// Home: the 12 modules in their 4 groups, as cards (from lib/modules.js).
import Link from "next/link";

import { GROUPS, STREAMLIT_STATUS, WEB_STATUS } from "@/lib/modules";

// "Ready" in green, "Coming soon" in amber.
function StatusPill({ status }) {
  const ready = status === "ready";
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${
      ready ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
        : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"}`}>
      {WEB_STATUS[status]}
    </span>
  );
}

export default function Home() {
  return (
    <div className="space-y-8">
      <section>
        <h1 className="text-3xl font-bold">📈 MarketLens</h1>
        <p className="mt-1 text-slate-600 dark:text-slate-400">
          Market Analytics System with an Algorithmic Trading Framework for Indian markets (NSE / BSE).
          Pick a module from the menu. Trading is paper (virtual money) unless TRADING_MODE=live.
        </p>
      </section>
      {GROUPS.map((group) => (
        <section key={group.name}>
          <h2 className="mb-3 text-lg font-semibold">{group.name}</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {group.modules.map((m) => (
              <Link key={m.href} href={m.href}
                className="flex flex-col gap-2 rounded-lg border border-slate-200 p-4 transition hover:border-blue-400
                  hover:shadow-sm dark:border-slate-800 dark:hover:border-blue-700">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="font-semibold leading-snug">
                    <span aria-hidden="true">{m.icon}</span> {m.num}. {m.title}
                  </h3>
                  <StatusPill status={m.web} />
                </div>
                <p className="text-sm text-slate-600 dark:text-slate-400">{m.description}</p>
                <p className="mt-auto text-xs text-slate-500">{STREAMLIT_STATUS[m.streamlit]}</p>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
