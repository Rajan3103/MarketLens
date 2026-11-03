// The root layout: wraps every page in the menu and top bar (components/AppShell.jsx).
import { Geist, Geist_Mono } from "next/font/google";

import AppShell from "@/components/AppShell";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata = {
  title: { default: "MarketLens", template: "%s · MarketLens" },
  description: "Market analytics and algorithmic trading framework for Indian markets (NSE / BSE).",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
