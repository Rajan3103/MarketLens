"use client";
// A table on TanStack Table: click a title to sort, a search box, show / hide columns, a header that stays on
// top while scrolling, pages of 25 / 50 / 100, numbers right-aligned in monospace, and "Download CSV" of
// exactly what is shown (search, sort and visible columns applied; every page).
//
//   <DataTable rows={rows} columns={[
//       { key: "date", label: "Date", kind: "date" },
//       { key: "close", label: "Close", kind: "rs" },
//       { key: "pnl", label: "P&L", kind: "rs", colorSign: true },     // green above 0, red below
//     ]}
//     initialSort={{ key: "date", dir: "desc" }} downloadName="trades"
//     highlightRow={(row) => row.strike === atm}                       // e.g. the ATM strike
//     rowActions={[{ label: "Open", icon: ExternalLink, onClick: (row) => open(row) }]} />
//
// kind: text, number, int, rs, rs_signed, pct, pct_signed, date, time. A column may give render(row) for its own
// cell, and hidden: true to start hidden. On phones the table scrolls sideways inside its card.
import {
  flexRender, getCoreRowModel, getFilteredRowModel, getPaginationRowModel, getSortedRowModel, useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Columns3, Download, Search, SearchX } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { saveBlob } from "@/components/DownloadButton";
import EmptyState from "@/components/EmptyState";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { formatValue, isEmpty, pnlClass } from "@/lib/format";

const NUMERIC = new Set(["number", "int", "rs", "rs_signed", "pct", "pct_signed"]);
const PAGE_SIZES = [25, 50, 100];

// Compare two cells: numbers as numbers, text as text; empty cells sort as the smallest.
export function compareCells(a, b) {
  if (isEmpty(a) && isEmpty(b)) return 0;
  if (isEmpty(a)) return -1;
  if (isEmpty(b)) return 1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), "en-IN", { numeric: true });
}

// One CSV cell: quoted when it holds a comma, quote or line break.
function csvCell(value) {
  if (isEmpty(value)) return "";
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

// The rows as CSV text (raw values, so Excel can still calculate with them).
export function toCsv(columns, rows) {
  const lines = [columns.map((c) => csvCell(c.label)).join(",")];
  rows.forEach((row) => lines.push(columns.map((c) => csvCell(row[c.key])).join(",")));
  return lines.join("\r\n");
}

// Search: a row matches when any visible cell (as raw text or as shown) contains the text.
function matches(row, columnId, filter, column) {
  const raw = row.getValue(columnId);
  const needle = String(filter).toLowerCase();
  const kind = column?.columnDef.meta?.kind;
  return String(raw ?? "").toLowerCase().includes(needle)
    || formatValue(raw, kind, column?.columnDef.meta?.decimals).toLowerCase().includes(needle);
}

// The little arrow in a column title: which way it is sorted (or that it can be).
function SortIcon({ dir }) {
  if (dir === "asc") return <ArrowUp className="size-3.5" aria-hidden="true" />;
  if (dir === "desc") return <ArrowDown className="size-3.5" aria-hidden="true" />;
  return <ArrowUpDown className="size-3.5 opacity-40" aria-hidden="true" />;
}

export default function DataTable({ columns = [], rows, pageSize = 25, initialSort = null, rowActions = [],
  emptyText = "Nothing to show.", rowKey = (row, i) => row.id ?? i, searchable = true, downloadName = "marketlens-table",
  highlightRow, maxHeight = "36rem", title, toolbar }) {
  const data = useMemo(() => rows || [], [rows]);
  const [sorting, setSorting] = useState(initialSort ? [{ id: initialSort.key, desc: initialSort.dir === "desc" }] : []);
  const [globalFilter, setGlobalFilter] = useState("");
  const [columnVisibility, setColumnVisibility] = useState(
    () => Object.fromEntries(columns.filter((c) => c.hidden).map((c) => [c.key, false])));
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize });
  const sizes = [...new Set([...PAGE_SIZES, pageSize])].sort((a, b) => a - b);

  const defs = useMemo(() => columns.map((c) => ({
    id: c.key,
    accessorFn: (row) => row[c.key],
    header: c.label,
    meta: { kind: c.kind, decimals: c.decimals, colorSign: c.colorSign, render: c.render, label: c.label },
    sortingFn: (a, b, id) => compareCells(a.getValue(id), b.getValue(id)),
    enableHiding: true,
  })), [columns]);

  // eslint-disable-next-line react-hooks/incompatible-library -- TanStack Table manages its own state
  const table = useReactTable({
    data,
    columns: defs,
    state: { sorting, globalFilter, columnVisibility, pagination },
    onSortingChange: (next) => { setSorting(next); setPagination((p) => ({ ...p, pageIndex: 0 })); },
    onGlobalFilterChange: (next) => { setGlobalFilter(next); setPagination((p) => ({ ...p, pageIndex: 0 })); },
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: setPagination,
    globalFilterFn: (row, columnId, filter) => matches(row, columnId, filter, table.getColumn(columnId)),
    getRowId: (row, i) => String(rowKey(row, i)),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  if (!data.length) return <EmptyState title={emptyText} />;

  const shownRows = table.getPrePaginationRowModel().rows;
  const visible = columns.filter((c) => table.getColumn(c.key)?.getIsVisible());
  const { pageIndex, pageSize: size } = table.getState().pagination;
  const first = shownRows.length ? pageIndex * size + 1 : 0;
  const last = Math.min((pageIndex + 1) * size, shownRows.length);

  // Save what is shown (search and sort applied, visible columns, every page) as a CSV file.
  const download = () => {
    const csv = toCsv(visible, shownRows.map((r) => r.original));
    saveBlob(new Blob(["﻿", csv], { type: "text/csv;charset=utf-8" }), `${downloadName}.csv`);
    toast.success("CSV saved", { description: `${shownRows.length} rows, ${visible.length} columns` });
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {title && <h3 className="mr-auto text-base font-semibold">{title}</h3>}
        {searchable && (
          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true" />
            <Input value={globalFilter} onChange={(e) => table.setGlobalFilter(e.target.value)} placeholder="Search…"
              aria-label="Search the table" className="pl-8" />
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          {toolbar}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm"><Columns3 aria-hidden="true" /> Columns</Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Show columns</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {table.getAllLeafColumns().map((col) => (
                <DropdownMenuCheckboxItem key={col.id} checked={col.getIsVisible()}
                  onCheckedChange={(on) => col.toggleVisibility(Boolean(on))} onSelect={(e) => e.preventDefault()}>
                  {col.columnDef.meta.label}
                </DropdownMenuCheckboxItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button variant="outline" size="sm" onClick={download} disabled={!shownRows.length}>
            <Download aria-hidden="true" /> CSV
          </Button>
        </div>
      </div>

      <div className="overflow-auto rounded-xl border bg-card" style={{ maxHeight }}>
        <table className="w-full caption-bottom text-sm">
          <thead className="sticky top-0 z-10 bg-card shadow-[inset_0_-1px_0_var(--border)]">
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => {
                  const meta = header.column.columnDef.meta;
                  const dir = header.column.getIsSorted();
                  return (
                    <th key={header.id} scope="col"
                      aria-sort={dir ? (dir === "asc" ? "ascending" : "descending") : "none"}
                      className={`h-10 whitespace-nowrap px-3 font-medium text-muted-foreground
                        ${NUMERIC.has(meta.kind) ? "text-right" : "text-left"}`}>
                      <button type="button" onClick={header.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1 hover:text-foreground">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <SortIcon dir={dir} />
                      </button>
                    </th>
                  );
                })}
                {rowActions.length > 0 && <th className="px-3" aria-label="Actions" />}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => {
              const highlighted = highlightRow?.(row.original);
              return (
                <tr key={row.id} data-highlight={highlighted || undefined}
                  className={`border-b last:border-0 hover:bg-muted/50 ${highlighted ? "bg-primary/10 font-medium" : ""}`}>
                  {row.getVisibleCells().map((cell) => {
                    const meta = cell.column.columnDef.meta;
                    const value = cell.getValue();
                    const numeric = NUMERIC.has(meta.kind);
                    return (
                      <td key={cell.id} className={`whitespace-nowrap px-3 py-2 ${numeric ? "num text-right" : ""}
                        ${meta.colorSign ? pnlClass(value) : ""}`}>
                        {meta.render ? meta.render(row.original) : formatValue(value, meta.kind, meta.decimals)}
                      </td>
                    );
                  })}
                  {rowActions.length > 0 && (
                    <td className="whitespace-nowrap px-3 py-1.5 text-right">
                      {rowActions.filter((a) => !a.hidden?.(row.original)).map(({ label, icon: Icon, onClick, variant }) => (
                        <Button key={label} size="xs" variant={variant || "outline"} className="ml-1"
                          onClick={() => onClick(row.original)}>
                          {Icon && <Icon aria-hidden="true" />} {label}
                        </Button>
                      ))}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
        {!shownRows.length && (
          <EmptyState icon={SearchX} title="No rows match the search" className="m-3 border-0" />
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
        <span className="num">
          {first}–{last} of {shownRows.length}{shownRows.length !== data.length ? ` (from ${data.length})` : ""}
        </span>
        <div className="flex items-center gap-2">
          <span>Rows</span>
          <Select value={String(size)} onValueChange={(v) => table.setPageSize(Number(v))}>
            <SelectTrigger size="sm" className="w-20" aria-label="Rows per page"><SelectValue /></SelectTrigger>
            <SelectContent>
              {sizes.map((s) => <SelectItem key={s} value={String(s)}>{s}</SelectItem>)}
            </SelectContent>
          </Select>
          <Button variant="outline" size="icon-sm" onClick={() => table.previousPage()}
            disabled={!table.getCanPreviousPage()} aria-label="Previous page"><ChevronLeft /></Button>
          <Button variant="outline" size="icon-sm" onClick={() => table.nextPage()}
            disabled={!table.getCanNextPage()} aria-label="Next page"><ChevronRight /></Button>
        </div>
      </div>
    </div>
  );
}
