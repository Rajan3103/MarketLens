"use client";
// A table with sorting (click a column title), pages, and numbers in Rs / % the Indian way.
//
//   <DataTable rows={rows} columns={[
//       { key: "date", label: "Date", kind: "date" },
//       { key: "close", label: "Close", kind: "rs" },
//       { key: "pnl", label: "P&L", kind: "rs", colorSign: true },     // green above 0, red below
//     ]}
//     pageSize={10} initialSort={{ key: "date", dir: "desc" }}
//     rowActions={[{ label: "Open", onClick: (row) => open(row) }]} />
//
// kind: text, number, int, rs, pct, pct_signed, date. A column may also give render(row) for its own cell.
import { useMemo, useState } from "react";

import { formatValue, isEmpty } from "@/lib/format";

const NUMERIC = new Set(["number", "int", "rs", "pct", "pct_signed"]);

// Compare two cells: numbers as numbers, text as text; empty cells always last.
function compare(a, b) {
  if (isEmpty(a) && isEmpty(b)) return 0;
  if (isEmpty(a)) return 1;
  if (isEmpty(b)) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), "en-IN", { numeric: true });
}

export default function DataTable({ columns, rows = [], pageSize = 10, initialSort = null, rowActions = [],
  emptyText = "Nothing to show.", rowKey = (row, i) => row.id ?? i }) {
  const [sort, setSort] = useState(initialSort);
  const [page, setPage] = useState(0);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const out = [...rows].sort((r1, r2) => compare(r1[sort.key], r2[sort.key]));
    return sort.dir === "desc" ? out.reverse() : out;
  }, [rows, sort]);

  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const current = Math.min(page, pages - 1);
  const shown = sorted.slice(current * pageSize, current * pageSize + pageSize);

  // Click a title: sort up, then down, then back to the original order.
  const toggle = (key) => {
    setPage(0);
    setSort((s) => (!s || s.key !== key ? { key, dir: "asc" } : s.dir === "asc" ? { key, dir: "desc" } : null));
  };

  if (!rows.length) return <p className="py-3 text-sm text-slate-500">{emptyText}</p>;
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600 dark:bg-slate-900 dark:text-slate-400">
            <tr>
              {columns.map((c) => {
                const active = sort?.key === c.key;
                return (
                  <th key={c.key} scope="col" aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
                    className={`px-3 py-2 font-medium ${NUMERIC.has(c.kind) ? "text-right" : "text-left"}`}>
                    <button onClick={() => toggle(c.key)} className="inline-flex items-center gap-1 hover:text-slate-900 dark:hover:text-white">
                      {c.label}<span aria-hidden="true">{active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}</span>
                    </button>
                  </th>
                );
              })}
              {rowActions.length > 0 && <th className="px-3 py-2" aria-label="Actions" />}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, i) => (
              <tr key={rowKey(row, current * pageSize + i)} className="border-t border-slate-200 dark:border-slate-800">
                {columns.map((c) => {
                  const value = row[c.key];
                  const sign = c.colorSign && !isEmpty(value) ? Math.sign(Number(value)) : 0;
                  return (
                    <td key={c.key} className={`whitespace-nowrap px-3 py-2 ${NUMERIC.has(c.kind) ? "text-right tabular-nums" : ""}
                      ${sign > 0 ? "text-green-600 dark:text-green-400" : sign < 0 ? "text-red-600 dark:text-red-400" : ""}`}>
                      {c.render ? c.render(row) : formatValue(value, c.kind, c.decimals)}
                    </td>
                  );
                })}
                {rowActions.length > 0 && (
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    {rowActions.map((a) => (
                      <button key={a.label} onClick={() => a.onClick(row)}
                        className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800">
                        {a.label}
                      </button>
                    ))}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-between text-sm text-slate-600 dark:text-slate-400">
          <span>
            {current * pageSize + 1}–{Math.min((current + 1) * pageSize, sorted.length)} of {sorted.length}
          </span>
          <span className="flex gap-2">
            <button disabled={current === 0} onClick={() => setPage(current - 1)}
              className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40 dark:border-slate-700">‹ Previous</button>
            <button disabled={current >= pages - 1} onClick={() => setPage(current + 1)}
              className="rounded-md border border-slate-300 px-3 py-1 disabled:opacity-40 dark:border-slate-700">Next ›</button>
          </span>
        </div>
      )}
    </div>
  );
}
