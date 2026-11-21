// The root layout: fonts, the light / dark theme, tooltips, toasts, and the menu and top bar
// (components/AppShell.jsx) around every page.
import { Inter, JetBrains_Mono } from "next/font/google";

import AppShell from "@/components/AppShell";
import QueryProvider from "@/components/QueryProvider";
import ThemeProvider from "@/components/ThemeProvider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import "./globals.css";

// Inter for text, JetBrains Mono for numbers and code (served from this app, no request to Google).
const inter = Inter({ variable: "--font-inter", subsets: ["latin"], display: "swap" });
const jetbrainsMono = JetBrains_Mono({ variable: "--font-jetbrains-mono", subsets: ["latin"], display: "swap" });

export const metadata = {
  title: { default: "MarketLens", template: "%s · MarketLens" },
  description: "Market analytics and algorithmic trading framework for Indian markets (NSE / BSE).",
};

// suppressHydrationWarning: next-themes adds class="dark" to <html> before React starts, on purpose.
export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning
      className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}>
      <body className="min-h-full">
        <ThemeProvider>
          <QueryProvider>
            <TooltipProvider>
              <AppShell>{children}</AppShell>
              <Toaster richColors closeButton position="bottom-right" />
            </TooltipProvider>
          </QueryProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
