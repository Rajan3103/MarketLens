// Module 8: Momentum Engine Backtest vs Nifty (the page itself is in MomentumEngine.jsx).
// It reads ?strategy= from the address, so it sits inside <Suspense>.
import { Suspense } from "react";

import Loading from "@/components/Loading";

import MomentumEngine from "./MomentumEngine";

export const metadata = { title: "Momentum Engine Backtest vs Nifty" };

export default function MomentumEnginePage() {
  return (
    <Suspense fallback={<Loading />}>
      <MomentumEngine />
    </Suspense>
  );
}
