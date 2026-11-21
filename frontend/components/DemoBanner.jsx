"use client";
// An amber "DEMO DATA" banner when the API shows made-up prices (no Dhan keys).
// Pass demo={true/false} when the page already knows; otherwise it asks /api/health (shared, cached).
import { FlaskConical } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useApi } from "@/lib/queries";

export default function DemoBanner({ demo }) {
  const health = useApi("health", { enabled: demo === undefined });
  const show = demo !== undefined ? demo : Boolean(health.data?.demo);
  if (!show) return null;
  return (
    <Alert className="border-demo/40 bg-demo/10 text-demo">
      <FlaskConical aria-hidden="true" />
      <AlertTitle>DEMO DATA</AlertTitle>
      <AlertDescription className="text-demo">
        The Dhan keys are not set, so these prices are made up for practice. Add DHAN_CLIENT_ID and
        DHAN_ACCESS_TOKEN to .env for real prices.
      </AlertDescription>
    </Alert>
  );
}
