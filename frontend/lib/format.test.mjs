// Tests for lib/format.js with Node's built-in test runner (no extra packages):
//     npm test            (from the frontend folder)
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  formatDate, formatInt, formatNumber, formatPct, formatRelative, formatRs, formatTimeIST, formatValue, isEmpty,
  pnlClass,
} from "./format.js";

// Indian grouping: lakhs and crores.
test("rupees and numbers use Indian grouping", () => {
  assert.equal(formatNumber(12345678.5), "1,23,45,678.50");
  assert.equal(formatRs(12345678.5), "Rs 1,23,45,678.50");
  assert.equal(formatRs(-1234), "-Rs 1,234.00");
  assert.equal(formatRs(500, 2, true), "+Rs 500.00");
  assert.equal(formatRs(0, 2, true), "Rs 0.00");
  assert.equal(formatRs(1234.567, 0), "Rs 1,235");
  assert.equal(formatInt(42318217), "4,23,18,217");
});

// Percentages are already in percent; the + sign is optional.
test("percentages with and without a sign", () => {
  assert.equal(formatPct(12.345), "12.35 %");
  assert.equal(formatPct(12.345, 2, true), "+12.35 %");
  assert.equal(formatPct(-3.2, 1, true), "-3.2 %");
  assert.equal(formatPct(0, 2, true), "0.00 %");
});

// Empty values show a dash everywhere.
test("empty values", () => {
  for (const empty of [null, undefined, "", Number.NaN]) {
    assert.ok(isEmpty(empty));
    for (const f of [formatNumber, formatRs, formatPct, formatInt, formatDate, formatTimeIST, formatRelative]) {
      assert.equal(f(empty), "–");
    }
  }
  assert.ok(!isEmpty(0));
});

// Dates look like 16-Nov-2026; a moment with a time shows its date in India.
test("dates", () => {
  assert.equal(formatDate("2026-11-16"), "16-Nov-2026");
  assert.equal(formatDate("2026-11-03"), "03-Nov-2026");
  assert.equal(formatDate("2026-11-15T20:00:00Z"), "16-Nov-2026");            // 01:30 IST on the 16th
  assert.equal(formatDate("2026-11-16T09:15:00+05:30"), "16-Nov-2026");
  assert.equal(formatDate(new Date("2026-01-05T06:00:00Z")), "05-Jan-2026");
  assert.equal(formatDate("not a date"), "not a date");
});

// Times are India time, 24-hour clock.
test("time in IST", () => {
  assert.equal(formatTimeIST("2026-11-16T09:15:00+05:30"), "09:15 IST");
  assert.equal(formatTimeIST("2026-11-16T10:00:00Z"), "15:30 IST");
  assert.equal(formatTimeIST(Date.UTC(2026, 10, 16, 18, 30)), "00:00 IST");
});

// "5 min ago" and friends, against a fixed "now".
test("relative times", () => {
  const now = Date.parse("2026-11-16T12:00:00Z");
  const before = (seconds) => new Date(now - seconds * 1000).toISOString();
  assert.equal(formatRelative(before(10), now), "just now");
  assert.equal(formatRelative(before(300), now), "5 min ago");
  assert.equal(formatRelative(before(3 * 3600), now), "3 h ago");
  assert.equal(formatRelative(before(86400), now), "1 day ago");
  assert.equal(formatRelative(before(2 * 86400), now), "2 days ago");
  assert.equal(formatRelative(new Date(now + 600 * 1000), now), "in 10 min");
  assert.equal(formatRelative(before(30 * 86400), now), "17-Oct-2026");
});

// Profit green, loss red, nothing grey.
test("pnlClass", () => {
  assert.equal(pnlClass(12.5), "text-profit");
  assert.equal(pnlClass("-3"), "text-loss");
  assert.equal(pnlClass(0), "text-muted-foreground");
  assert.equal(pnlClass(null), "text-muted-foreground");
});

// formatValue still knows every old kind, plus the new ones.
test("formatValue kinds", () => {
  assert.equal(formatValue(1500, "rs"), "Rs 1,500.00");
  assert.equal(formatValue(1500, "rs_signed"), "+Rs 1,500.00");
  assert.equal(formatValue(2.5, "pct_signed"), "+2.50 %");
  assert.equal(formatValue(2.5, "pct"), "2.50 %");
  assert.equal(formatValue(1500, "int"), "1,500");
  assert.equal(formatValue(1.23456, "number", 3), "1.235");
  assert.equal(formatValue("2026-11-16", "date"), "16-Nov-2026");
  assert.equal(formatValue("2026-11-16T09:15:00+05:30", "time"), "09:15 IST");
  assert.equal(formatValue("hello"), "hello");
});
