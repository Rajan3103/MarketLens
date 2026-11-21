"use client";
// A label with its unit badge (Rs, %, days, min), an optional help tooltip, the input itself, then a hint or an
// error in red under it.
//
//   <FormField label="Capital" unit="Rs" htmlFor="cap" help="Money at the start" error={errors.cap}>
//     <NumberInput id="cap" … />
//   </FormField>
import { Info } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export default function FormField({ label, unit, hint, help, error, htmlFor, children, className = "" }) {
  const describedBy = htmlFor && (error || hint) ? `${htmlFor}-note` : undefined;
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <div className="flex items-center gap-1.5">
        <Label htmlFor={htmlFor}>{label}</Label>
        {unit && <Badge variant="secondary" className="num h-5 px-1.5 text-[0.7rem]">{unit}</Badge>}
        {help && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" className="text-muted-foreground hover:text-foreground" aria-label={`About ${label}`}>
                <Info className="size-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">{help}</TooltipContent>
          </Tooltip>
        )}
      </div>
      {children}
      {error ? (
        <p id={describedBy} role="alert" className="text-xs font-medium text-loss">{error}</p>
      ) : hint ? (
        <p id={describedBy} className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

// The same look as the shadcn Input, for plain <input> and <select> boxes on older pages.
export const INPUT_CLASS = "h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs " +
  "outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30 " +
  "disabled:opacity-50";
