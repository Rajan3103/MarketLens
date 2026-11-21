// "Please wait": a spinner with a short text, plus grey shapes where the content will appear.
//
//   <Loading />                         a spinner line
//   <Loading text="Loading prices…" variant="chart" />     variant: line | cards | table | chart
import { Loader2 } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";

// The grey placeholder shapes for each kind of content.
function Shapes({ variant }) {
  if (variant === "cards") {
    return (
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24 rounded-xl" />)}
      </div>
    );
  }
  if (variant === "table") {
    return (
      <div className="space-y-2">
        <Skeleton className="h-9 w-full" />
        {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-7 w-full" />)}
      </div>
    );
  }
  if (variant === "chart") return <Skeleton className="h-72 w-full rounded-xl" />;
  return null;
}

export default function Loading({ text = "Loading…", variant = "line" }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3 py-4">
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        {text}
      </p>
      <Shapes variant={variant} />
    </div>
  );
}
