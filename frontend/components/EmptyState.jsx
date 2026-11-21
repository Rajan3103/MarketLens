// "Nothing here yet": an icon, a short text and an optional button, in place of an empty table or chart.
//
//   <EmptyState icon={Inbox} title="No trades yet" text="Run a backtest to see trades here."
//     action={<Button onClick={run}>Run backtest</Button>} />
import { Inbox } from "lucide-react";

export default function EmptyState({ icon: Icon = Inbox, title = "Nothing to show yet", text, action, className = "" }) {
  return (
    <div className={`flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-10
      text-center ${className}`}>
      <Icon className="size-8 text-muted-foreground" aria-hidden="true" />
      <p className="font-medium">{title}</p>
      {text && <p className="max-w-md text-sm text-muted-foreground">{text}</p>}
      {action && <div className="pt-2">{action}</div>}
    </div>
  );
}
