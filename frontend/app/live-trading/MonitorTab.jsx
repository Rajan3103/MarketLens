"use client";
// Monitor tab: orders waiting for your Confirm (with Confirm / Reject), running strategies with today's
// P&L, open positions and today's orders. Data: GET /api/live/monitor, every 10 s in market hours.
import { useState } from "react";

import DataTable from "@/components/DataTable";
import ErrorBox from "@/components/ErrorBox";
import Loading from "@/components/Loading";
import { apiGet, apiPost } from "@/lib/api";
import usePolling from "@/lib/usePolling";

const RUNNING_COLUMNS = [
  { key: "name", label: "Strategy", kind: "text" },
  { key: "mode", label: "Mode", kind: "text", render: (r) => r.mode.toUpperCase() },
  { key: "status", label: "Status", kind: "text" },
  { key: "state", label: "State", kind: "text" },
  { key: "pnl", label: "Today's P&L", kind: "rs", decimals: 0, colorSign: true },
  { key: "lots_multiplier", label: "Lots ×", kind: "int" },
  { key: "message", label: "Message", kind: "text" },
];
const POSITION_COLUMNS = [
  { key: "strategy", label: "Strategy", kind: "text" },
  { key: "symbol", label: "Symbol", kind: "text" },
  { key: "product", label: "Product", kind: "text" },
  { key: "qty", label: "Qty", kind: "int" },
  { key: "avg_price", label: "Avg price", kind: "rs" },
  { key: "ltp", label: "Last price", kind: "rs" },
  { key: "unrealized_pnl", label: "Open P&L", kind: "rs", colorSign: true },
  { key: "realized_pnl", label: "Booked P&L", kind: "rs", colorSign: true },
];
const ORDER_COLUMNS = [
  { key: "placed_at", label: "Time", kind: "text" },
  { key: "strategy", label: "Strategy", kind: "text" },
  { key: "symbol", label: "Symbol", kind: "text" },
  { key: "side", label: "Side", kind: "text" },
  { key: "qty", label: "Qty", kind: "int" },
  { key: "status", label: "Status", kind: "text" },
  { key: "broker_order_id", label: "Broker order id", kind: "text" },
  { key: "avg_fill_price", label: "Fill price", kind: "rs" },
  { key: "reason", label: "Reason", kind: "text" },
  { key: "message", label: "Message", kind: "text" },
];

// A heading.
function Heading({ children }) {
  return <h2 className="text-lg font-semibold">{children}</h2>;
}

export default function MonitorTab({ marketOpen }) {
  const { data, error, refresh } = usePolling(() => apiGet("live/monitor"), 10, { enabled: marketOpen });
  const [answer, setAnswer] = useState(null);
  const [problem, setProblem] = useState(null);

  // Confirm or reject one waiting signal.
  const act = async (signalId, what) => {
    setAnswer(null);
    setProblem(null);
    try {
      const result = await apiPost(`live/signals/${signalId}/${what}`);
      setAnswer(`${result.status}: ${result.message}`);
      refresh();
    } catch (err) {
      setProblem(err);
    }
  };

  const waitingColumns = [
    { key: "signal_id", label: "Signal", kind: "int" },
    { key: "placed_at", label: "Time", kind: "text" },
    { key: "strategy", label: "Strategy", kind: "text" },
    { key: "legs", label: "Orders", kind: "text" },
    { key: "reason", label: "Reason", kind: "text" },
    { key: "minutes_waiting", label: "Waiting (min)", kind: "number", decimals: 1 },
    { key: "actions", label: "", kind: "text", render: (r) => (
      <div className="flex gap-2">
        <button onClick={() => act(r.signal_id, "confirm")}
          className="rounded-md bg-green-600 px-3 py-1 text-xs font-semibold text-white hover:bg-green-700">✅ Confirm</button>
        <button onClick={() => act(r.signal_id, "reject")}
          className="rounded-md border border-red-400 px-3 py-1 text-xs font-semibold text-red-700 hover:bg-red-50">❌ Reject</button>
      </div>) },
  ];

  if (error && !data) return <ErrorBox error={error} onRetry={refresh} />;
  if (!data) return <Loading text="Reading orders and positions…" />;
  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <Heading>Orders waiting for your confirmation</Heading>
        {answer && <p className="text-sm">{answer}</p>}
        {problem && <ErrorBox error={problem} />}
        <DataTable columns={waitingColumns} rows={data.waiting} rowKey={(r) => r.signal_id} emptyText="None." />
      </section>
      <section className="space-y-2">
        <Heading>Running strategies (today&apos;s P&amp;L)</Heading>
        <DataTable columns={RUNNING_COLUMNS} rows={data.running} rowKey={(r) => r.strategy_id}
          emptyText="No strategy is deployed and running." />
      </section>
      <section className="space-y-2">
        <Heading>Open positions</Heading>
        <p className="text-xs text-slate-500">{data.positions_note}</p>
        <DataTable columns={POSITION_COLUMNS} rows={data.positions} rowKey={(r, i) => `${r.strategy}-${r.symbol}-${i}`}
          emptyText="None." />
      </section>
      <section className="space-y-2">
        <Heading>Today&apos;s orders</Heading>
        <DataTable columns={ORDER_COLUMNS} rows={data.orders} pageSize={20} rowKey={(r, i) => `${r.placed_at}-${r.symbol}-${i}`}
          emptyText="No orders today." />
      </section>
    </div>
  );
}
