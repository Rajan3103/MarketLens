"use client";
// A short explanation that opens and closes ("What this page does / how to read it"). Whether it is open is
// remembered in this browser per page, so a note you closed stays closed.
//
//   <HelpNote what="Backtests a momentum portfolio." how="Green is profit after costs and tax." />
//   <HelpNote title="What is max pain?">…any text…</HelpNote>
import { ChevronDown, CircleHelp } from "lucide-react";
import { usePathname } from "next/navigation";

import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import useStoredState from "@/lib/useStoredState";

export default function HelpNote({ title = "What this page does / how to read it", what, how, children, open = false,
  storageKey }) {
  const pathname = usePathname();
  const [isOpen, setOpen] = useStoredState(`marketlens:help:${storageKey || `${pathname}:${title}`}`, open);
  return (
    <Collapsible open={isOpen} onOpenChange={setOpen} className="rounded-lg border bg-card text-sm">
      <CollapsibleTrigger className="flex w-full items-center gap-2 px-4 py-2 text-left font-medium
        hover:bg-muted/50 focus-visible:outline-2">
        <CircleHelp className="size-4 text-primary" aria-hidden="true" />
        <span className="flex-1">{title}</span>
        <ChevronDown className={`size-4 transition-transform ${isOpen ? "rotate-180" : ""}`} aria-hidden="true" />
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-2 px-4 pb-3 text-muted-foreground">
        {what && <p><span className="font-medium text-foreground">What this page does: </span>{what}</p>}
        {how && <p><span className="font-medium text-foreground">How to read it: </span>{how}</p>}
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}
