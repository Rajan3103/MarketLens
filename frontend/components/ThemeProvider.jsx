"use client";
// Light / dark / system theme for the whole app (next-themes). It writes class="dark" on <html> from a tiny
// script that runs before the first paint, so the page never flashes the wrong colours on load.
import { ThemeProvider as NextThemesProvider } from "next-themes";

// Wraps the app in app/layout.jsx. Dark is the default; "system" follows the computer's setting.
export default function ThemeProvider({ children }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
      {children}
    </NextThemesProvider>
  );
}
