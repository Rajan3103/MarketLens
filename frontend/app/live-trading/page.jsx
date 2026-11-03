// Module 11: Live Algo Trading & Bot Monitor (the page itself is in LiveTrading.jsx).
// It reads ?strategy= from the address (Strategy Library -> Deploy live), so it sits inside <Suspense>.
import { Suspense } from "react";

import Loading from "@/components/Loading";

import LiveTrading from "./LiveTrading";

export const metadata = { title: "Live Algo Trading & Bot Monitor" };

export default function LiveTradingPage() {
  return (
    <Suspense fallback={<Loading />}>
      <LiveTrading />
    </Suspense>
  );
}
