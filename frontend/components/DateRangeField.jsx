"use client";
// A "From - To" date picker on a calendar (two months side by side on wide screens).
// Values are plain dates as text ("2026-01-01"), the same as the API uses.
//
//   <DateRangeField label="Backtest period" value={{ from: "2025-01-01", to: "2025-12-31" }} onChange={setRange} />
import { CalendarDays } from "lucide-react";

import FormField from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDate } from "@/lib/format";

// "2026-01-05" -> a Date at local midnight (no time-zone shift), or undefined.
export function toDate(text) {
  if (!text) return undefined;
  const [y, m, d] = String(text).slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

// A Date -> "2026-01-05" (its local calendar date), or "".
export function toText(date) {
  if (!date) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export default function DateRangeField({ id, label = "Dates", value = {}, onChange, min, max, help, hint, error,
  className = "" }) {
  const selected = { from: toDate(value.from), to: toDate(value.to) };
  const text = value.from ? `${formatDate(value.from)} – ${value.to ? formatDate(value.to) : "…"}` : "Pick the dates";
  return (
    <FormField label={label} htmlFor={id} help={help} hint={hint} error={error} className={className}>
      <Popover>
        <PopoverTrigger asChild>
          <Button id={id} variant="outline" className="num w-full justify-start font-normal">
            <CalendarDays aria-hidden="true" /> {text}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <Calendar mode="range" numberOfMonths={2} selected={selected} defaultMonth={selected.from}
            captionLayout="dropdown" startMonth={toDate(min)} endMonth={toDate(max)}
            disabled={[...(min ? [{ before: toDate(min) }] : []), ...(max ? [{ after: toDate(max) }] : [])]}
            onSelect={(range) => onChange({ from: toText(range?.from), to: toText(range?.to) })} />
        </PopoverContent>
      </Popover>
    </FormField>
  );
}
