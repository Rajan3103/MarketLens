// /ui-kit: every design-system component with sample data, to see the look in both themes.
// Only while developing (npm run dev); in a production build (npm run build / start) it answers "not found".
import { notFound } from "next/navigation";

import UiKit from "./UiKit";

export const metadata = { title: "UI kit" };

// The page: the showcase in development, 404 everywhere else.
export default function UiKitPage() {
  if (process.env.NODE_ENV !== "development") notFound();
  return <UiKit />;
}
