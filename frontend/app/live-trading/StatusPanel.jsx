"use client";
// The big PAPER / LIVE banner (with AUTO_TRADE and the kill switch) and the six status cards:
// Dhan login, live runner, API server, Telegram bot, scheduler and database. Data: GET /api/live/status.
import { useState } from "react";

import ErrorBox from "@/components/ErrorBox";
import { apiPost } from "@/lib/api";

// PAPER (blue) or LIVE (red), in big letters.
export function Banner({ status }) {
  const live = status.live;
  return (
    <div className={`rounded-xl px-5 py-4 text-white ${live ? "bg-red-700" : "bg-blue-700"}`}>
      <p className="text-2xl font-extrabold tracking-wide">{live ? "LIVE: REAL MONEY" : "PAPER: VIRTUAL MONEY"}</p>
      <p className="mt-1 text-sm">
        {status.auto_trade ? "AUTO_TRADE on: live orders go without asking"
          : "AUTO_TRADE off: every live order waits for your Confirm"}
        {" · "}
        {status.kill_switch.on ? <strong>🛑 KILL SWITCH ON ({status.kill_switch.reason})</strong> : "Kill switch off"}
        {" · "}
        {status.market_open ? "Market open" : "Market closed"}
      </p>
      {status.kill_switch.on && (
        <p className="mt-2 rounded bg-black/20 px-3 py-1 text-sm">
          No new orders today. Exits (SL / target / Stop) still go through. Switch it off in the Loss limits tab.
        </p>
      )}
    </div>
  );
}

// One status card: title, green / red / grey dot and a short line.
function Card({ title, ok, text, children }) {
  const dot = ok === true ? "bg-green-500" : ok === false ? "bg-red-500" : "bg-slate-400";
  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{title}</p>
      <p className="mt-1 flex items-start gap-2 text-sm">
        <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} aria-hidden />
        <span>{text}</span>
      </p>
      {children}
    </div>
  );
}

// The six cards, and the renew steps when today's Dhan login is not valid.
export function StatusCards({ status, onChanged }) {
  const c = status.cards;
  const [answer, setAnswer] = useState(null);
  const [problem, setProblem] = useState(null);
  const [busy, setBusy] = useState(false);
  const login = c.login;
  const expiry = login.expiry ? new Date(login.expiry).toLocaleString("en-IN", { timeZone: "Asia/Kolkata",
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : null;

  // Ask Dhan now whether today's login works.
  const check = async () => {
    setBusy(true);
    setProblem(null);
    try {
      setAnswer(await apiPost("live/login/check"));
      onChanged?.();
    } catch (err) {
      setProblem(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Card title="Dhan login today" ok={login.ok}
          text={login.ok ? `Valid${expiry ? ` until ${expiry}` : ""}` : login.status === "not checked" ? "Not checked today"
            : `${login.status}: ${login.text}`} />
        <Card title="Live runner" ok={c.runner.ok} text={c.runner.text} />
        <Card title="API server" ok={c.api.ok} text={c.api.text} />
        <Card title="Telegram bot" ok={c.telegram.ok}
          text={`${c.telegram.text}${c.telegram.configured ? "" : " · keys not set"}`} />
        <Card title="Scheduler" ok={c.scheduler.ok} text={c.scheduler.text} />
        <Card title="Database" ok={c.database.ok} text={c.database.text} />
      </div>
      {!login.ok && (
        <details open={status.live} className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800 dark:bg-amber-950/40">
          <summary className="cursor-pointer font-semibold">🔑 Dhan login not valid today: how to renew (needs you, because of 2FA)</summary>
          <pre className="mt-3 whitespace-pre-wrap font-mono text-xs">{login.renew_steps}</pre>
          <button onClick={check} disabled={busy}
            className="mt-3 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900">
            {busy ? "Checking…" : "Check the login now"}
          </button>
          {answer && <p className={`mt-2 ${answer.ok ? "text-green-700" : "text-red-700"}`}>{answer.status}: {answer.message}</p>}
          {problem && <div className="mt-2"><ErrorBox error={problem} /></div>}
        </details>
      )}
    </div>
  );
}
