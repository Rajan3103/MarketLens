"use client";
// The frame around every page: the left menu (a menu button on phones) and the top bar with the badges.
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";
import { GROUPS, START_PAGES } from "@/lib/modules";

// One link in the menu, highlighted when it is the page you are on.
function MenuLink({ href, icon, label, active, onClick }) {
  return (
    <Link
      href={href}
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={`flex items-start gap-2 rounded-md px-3 py-1.5 text-sm leading-snug ${
        active ? "bg-blue-50 font-semibold text-blue-800 dark:bg-blue-950 dark:text-blue-200"
          : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
      }`}
    >
      <span aria-hidden="true">{icon}</span>
      <span>{label}</span>
    </Link>
  );
}

// The left menu: Start, then the 12 modules in their 4 groups (from lib/modules.js).
function Sidebar({ open, onNavigate }) {
  const pathname = usePathname();
  const sections = [{ name: "Start", items: START_PAGES.map((p) => ({ ...p, label: p.title })) },
    ...GROUPS.map((g) => ({ name: g.name, items: g.modules.map((m) => ({ ...m, label: `${m.num}. ${m.title}` })) }))];
  return (
    <nav
      aria-label="Main menu"
      className={`${open ? "block" : "hidden"} fixed inset-y-0 left-0 z-30 w-72 overflow-y-auto border-r border-slate-200
        bg-white px-3 pb-6 pt-16 dark:border-slate-800 dark:bg-slate-950 md:sticky md:top-0 md:block md:h-screen md:pt-4`}
    >
      <Link href="/" onClick={onNavigate} className="mb-4 hidden items-center gap-2 px-3 text-lg font-bold md:flex">
        📈 MarketLens
      </Link>
      {sections.map((section) => (
        <div key={section.name} className="mb-4">
          <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">{section.name}</p>
          {section.items.map((item) => (
            <MenuLink key={item.href} href={item.href} icon={item.icon} label={item.label}
              active={pathname === item.href} onClick={onNavigate} />
          ))}
        </div>
      ))}
    </nav>
  );
}

// A small coloured label.
function Badge({ className, children, title }) {
  return (
    <span title={title} className={`rounded-full px-3 py-1 text-xs font-bold tracking-wide ${className}`}>
      {children}
    </span>
  );
}

// PAPER (blue) or LIVE (red), and DEMO DATA, from GET /api/health (checked again every 30 seconds).
function HealthBadges() {
  const [health, setHealth] = useState(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let stopped = false;
    const check = () => apiGet("health")
      .then((h) => { if (!stopped) { setHealth(h); setProblem(""); } })
      .catch((err) => { if (!stopped) { setHealth(null); setProblem(err.message); } });
    check();
    const timer = setInterval(check, 30000);
    return () => { stopped = true; clearInterval(timer); };
  }, []);

  if (problem) {
    return <Badge className="bg-amber-100 text-amber-900" title={problem}>API NOT RUNNING</Badge>;
  }
  if (!health) return <Badge className="bg-slate-100 text-slate-500">checking…</Badge>;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {health.mode === "LIVE"
        ? <Badge className="bg-red-600 text-white" title="Orders use real money">LIVE</Badge>
        : <Badge className="bg-blue-600 text-white" title="Orders use virtual money only">PAPER</Badge>}
      {health.demo && <Badge className="bg-yellow-300 text-yellow-950" title="No Dhan keys: made-up prices">DEMO DATA</Badge>}
      {health.database !== "ok" && <Badge className="bg-red-100 text-red-800">DATABASE DOWN</Badge>}
    </div>
  );
}

// Menu + top bar + the page itself.
export default function AppShell({ children }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="flex min-h-screen">
      <Sidebar open={menuOpen} onNavigate={() => setMenuOpen(false)} />
      {menuOpen && (
        <button aria-label="Close the menu" onClick={() => setMenuOpen(false)}
          className="fixed inset-0 z-20 bg-black/30 md:hidden" />
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-40 flex items-center justify-between gap-3 border-b border-slate-200 bg-white/90
          px-4 py-3 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMenuOpen(!menuOpen)}
              aria-label={menuOpen ? "Close the menu" : "Open the menu"}
              aria-expanded={menuOpen}
              className="rounded-md border border-slate-300 px-2.5 py-1 text-lg md:hidden dark:border-slate-700"
            >
              {menuOpen ? "✕" : "☰"}
            </button>
            <Link href="/" className="font-bold md:hidden">📈 MarketLens</Link>
          </div>
          <HealthBadges />
        </header>
        <main className="w-full max-w-6xl flex-1 px-4 py-6 md:px-8">{children}</main>
        <footer className="px-4 pb-6 text-xs text-slate-500 md:px-8">
          Historical analysis for learning, not a live trading recommendation.
        </footer>
      </div>
    </div>
  );
}
