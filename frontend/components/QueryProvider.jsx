"use client";
// Gives every page the shared data cache of TanStack Query (lib/queries.js). One client per browser tab.
import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { makeQueryClient } from "@/lib/queries";

// Wraps the app in app/layout.jsx.
export default function QueryProvider({ children }) {
  const [client] = useState(makeQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
