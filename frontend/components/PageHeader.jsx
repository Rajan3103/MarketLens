// The top of every module page: icon, "4. Options Backtester", one line about the page, buttons on the right,
// and the help note under it.
//
//   <PageHeader icon={LineChart} number={4} title="Options Backtester" description="Multi-leg strategies on…"
//     actions={<Button>Run</Button>} help={{ what: "…", how: "…" }} />
import HelpNote from "@/components/HelpNote";

export default function PageHeader({ icon: Icon, number, title, description, actions, help, children }) {
  return (
    <header className="mb-6 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          {Icon && (
            <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Icon className="size-5" aria-hidden="true" />
            </span>
          )}
          <div className="min-w-0">
            <h1 className="text-2xl font-bold leading-tight">
              {number !== undefined && <span className="text-muted-foreground">{number}. </span>}
              {title}
            </h1>
            {description && <p className="text-sm text-muted-foreground">{description}</p>}
          </div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {help && <HelpNote what={help.what} how={help.how} storageKey={`page:${title}`}>{help.more}</HelpNote>}
      {children}
    </header>
  );
}
