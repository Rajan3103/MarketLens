"use client";
// Type part of a name or symbol and pick from the matches (GET /api/market/symbols?q=…): stocks, ETFs and
// indices, at most 20, best first.
//
//   <TickerSearch value={symbol} onChange={(match) => setSymbol(match.yahoo)} />
//   match = { symbol: "RELIANCE", yahoo: "RELIANCE.NS", name: "Reliance Industries Ltd.", kind: "stock", lists }
import { ChevronsUpDown, Loader2, Search } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useApi } from "@/lib/queries";

const KIND_LABEL = { index: "Index", stock: "Stock", etf: "ETF" };

// The text, but only once the user has stopped typing for `ms` milliseconds.
function useSettled(text, ms = 200) {
  const [settled, setSettled] = useState(text);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(text), ms);
    return () => clearTimeout(timer);
  }, [text, ms]);
  return settled;
}

export default function TickerSearch({ id, value, onChange, placeholder = "Search a stock, ETF or index…",
  className = "" }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const query = useSettled(text.trim());
  const { data, error, isFetching } = useApi(query ? `market/symbols?q=${encodeURIComponent(query)}` : null);
  const matches = query ? data?.matches || [] : [];

  // Pick one match: tell the page and close the list.
  const pick = (match) => {
    onChange?.(match);
    setOpen(false);
    setText("");
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button id={id} variant="outline" role="combobox" aria-expanded={open}
          className={`w-full justify-between font-normal ${className}`}>
          <span className="flex min-w-0 items-center gap-2">
            <Search className="text-muted-foreground" aria-hidden="true" />
            <span className={`truncate ${value ? "" : "text-muted-foreground"}`}>{value || placeholder}</span>
          </span>
          <ChevronsUpDown className="opacity-50" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-(--radix-popover-trigger-width) min-w-72 p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput placeholder="RELIANCE, nifty, gold…" value={text} onValueChange={setText} />
          <CommandList>
            {isFetching && !matches.length && (
              <p className="flex items-center gap-2 px-3 py-4 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Searching…
              </p>
            )}
            {error && <p className="px-3 py-4 text-sm text-loss">{error.message}</p>}
            {!query && <p className="px-3 py-4 text-sm text-muted-foreground">Start typing to search.</p>}
            {query && !isFetching && !error && <CommandEmpty>No match for “{query}”.</CommandEmpty>}
            {matches.length > 0 && (
              <CommandGroup>
                {matches.map((m) => (
                  <CommandItem key={m.symbol} value={m.symbol} onSelect={() => pick(m)}>
                    <span className="num w-28 shrink-0 truncate font-medium">{m.symbol}</span>
                    <span className="min-w-0 flex-1 truncate text-muted-foreground">{m.name}</span>
                    <Badge variant="outline" className="shrink-0">{KIND_LABEL[m.kind] || m.kind}</Badge>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
