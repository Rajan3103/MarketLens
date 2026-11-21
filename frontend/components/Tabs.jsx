"use client";
// Tabs whose open tab is kept in the address (?tab=chain), so a reload or a shared link opens the same tab.
// On phones the row of tabs scrolls sideways.
//
//   <Tabs tabs={[{ id: "chain", label: "Option chain", icon: Table, content: <Chain /> },
//                { id: "builder", label: "Strategy builder", content: <Builder /> }]} />
//
// Only the open tab is drawn. param="view" uses ?view=... instead (for two tab rows on one page).
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { Tabs as TabsRoot, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// The tab row and the open tab (reads the address, so it sits inside <Suspense>).
function TabsInner({ tabs, param }) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const wanted = search.get(param);
  const open = (tabs.find((t) => t.id === wanted) || tabs[0]).id;

  // Open a tab: change ?tab= in the address without scrolling or adding a history entry.
  const choose = (id) => {
    const next = new URLSearchParams(search.toString());
    next.set(param, id);
    router.replace(`${pathname}?${next}`, { scroll: false });
  };

  return (
    <TabsRoot value={open} onValueChange={choose} className="gap-4">
      <div className="-mx-1 overflow-x-auto px-1 pb-1">
        <TabsList>
          {tabs.map(({ id, label, icon: Icon }) => (
            <TabsTrigger key={id} value={id} className="whitespace-nowrap">
              {Icon && <Icon aria-hidden="true" />} {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
      {tabs.map((t) => (
        <TabsContent key={t.id} value={t.id}>{t.id === open ? t.content : null}</TabsContent>
      ))}
    </TabsRoot>
  );
}

export default function Tabs({ tabs, param = "tab" }) {
  if (!tabs?.length) return null;
  return (
    <Suspense fallback={<div className="pt-4">{tabs[0].content}</div>}>
      <TabsInner tabs={tabs.filter(Boolean)} param={param} />
    </Suspense>
  );
}
