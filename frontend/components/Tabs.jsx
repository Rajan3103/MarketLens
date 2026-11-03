"use client";
// Tabs whose open tab is kept in the address (?tab=chain), so a reload or a shared link opens the same tab.
//
//   <Tabs tabs={[{ id: "chain", label: "Option chain", content: <Chain /> },
//                { id: "builder", label: "Strategy builder", content: <Builder /> }]} />
//
// Only the open tab is drawn. param="view" uses ?view=... instead (for two tab rows on one page).
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

// The tab buttons and the open tab (reads the address, so it sits inside <Suspense>).
function TabsInner({ tabs, param }) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const wanted = search.get(param);
  const open = tabs.find((t) => t.id === wanted) || tabs[0];

  // Open a tab: change ?tab= in the address without scrolling or adding a new history entry.
  const choose = (id) => {
    const next = new URLSearchParams(search.toString());
    next.set(param, id);
    router.replace(`${pathname}?${next}`, { scroll: false });
  };

  return (
    <div>
      <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800">
        {tabs.map((t) => {
          const active = t.id === open.id;
          return (
            <button key={t.id} role="tab" aria-selected={active} onClick={() => choose(t.id)}
              className={`whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium ${active
                ? "border-blue-600 text-blue-700 dark:text-blue-300"
                : "border-transparent text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"}`}>
              {t.label}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" className="pt-4">{open.content}</div>
    </div>
  );
}

export default function Tabs({ tabs, param = "tab" }) {
  if (!tabs?.length) return null;
  return (
    <Suspense fallback={<div className="pt-4">{tabs[0].content}</div>}>
      <TabsInner tabs={tabs} param={param} />
    </Suspense>
  );
}
