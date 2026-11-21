// A red alert with a readable problem (an ApiError's message) and an optional "Try again" button.
//
//   <ErrorBox error={err} onRetry={refresh} />      or      <ErrorBox message="Pick at least one leg." />
import { RotateCw, TriangleAlert } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

export default function ErrorBox({ error, message, onRetry, title = "Something went wrong" }) {
  const text = message || error?.message || String(error || "Something went wrong.");
  return (
    <Alert variant="destructive">
      <TriangleAlert aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{text}</p>
        {onRetry && (
          <Button variant="outline" size="sm" className="mt-2" onClick={() => onRetry()}>
            <RotateCw aria-hidden="true" /> Try again
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}
