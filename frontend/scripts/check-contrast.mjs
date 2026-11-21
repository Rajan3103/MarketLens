// Checks the colour contrast of the theme in app/globals.css (WCAG: text needs at least 4.5 to 1).
//
//     node scripts/check-contrast.mjs        (from the frontend folder)
//
// For the light (:root) and dark (.dark) themes it checks every text colour on background, card and muted,
// on its own 10 % tint (text-profit on bg-profit/10), each -foreground on its solid fill, and the chart colours
// (at least 3 to 1, the rule for lines and shapes). Exit code 1 when anything is too low.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const CSS = readFileSync(fileURLToPath(new URL("../app/globals.css", import.meta.url)), "utf8");
const TEXT = ["foreground", "muted-foreground", "primary", "profit", "loss", "warning", "live", "paper", "demo"];
const SOLID = ["primary", "profit", "loss", "warning", "live", "paper", "demo"];
const SURFACES = ["background", "card", "muted"];
const CHARTS = ["chart-1", "chart-2", "chart-3", "chart-4", "chart-5"];

// The "--name: #rrggbb" pairs inside the first block that starts with `selector {`.
function variables(selector) {
  const start = CSS.indexOf(`${selector} {`);
  const block = CSS.slice(start, CSS.indexOf("}", start));
  return Object.fromEntries([...block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));
}

// Relative brightness of a #rrggbb colour (WCAG formula).
function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// Contrast ratio between two colours (1 to 21).
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// `share` of colour a laid over colour b (like bg-profit/10 on the page).
function mix(a, b, share) {
  const part = (hex, i) => parseInt(hex.slice(i, i + 2), 16);
  return "#" + [1, 3, 5].map((i) => Math.round(part(a, i) * share + part(b, i) * (1 - share))
    .toString(16).padStart(2, "0")).join("");
}

let failed = 0;
// Print one check and count it when it is below the minimum.
function check(theme, label, ratio, minimum) {
  const ok = ratio >= minimum;
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "LOW "} ${theme.padEnd(5)} ${label.padEnd(44)} ${ratio.toFixed(2)} (need ${minimum})`);
}

for (const [theme, selector] of [["light", ":root"], ["dark", ".dark"]]) {
  const v = variables(selector);
  for (const name of TEXT) {
    for (const surface of SURFACES) {
      check(theme, `${name} on ${surface}`, contrast(v[name], v[surface]), 4.5);
      check(theme, `${name} on ${name}/10 over ${surface}`, contrast(v[name], mix(v[name], v[surface], 0.1)), 4.5);
    }
  }
  for (const name of SOLID) check(theme, `${name}-foreground on ${name}`, contrast(v[`${name}-foreground`], v[name]), 4.5);
  for (const name of CHARTS) {
    for (const surface of ["background", "card"]) check(theme, `${name} on ${surface}`, contrast(v[name], v[surface]), 3);
  }
}
console.log(failed ? `\n${failed} check(s) too low.` : "\nEvery colour pair passes.");
process.exit(failed ? 1 : 0);
