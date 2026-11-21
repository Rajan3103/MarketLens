"use client";
// A small value remembered in this browser (localStorage), e.g. whether a help note is open.
//
//   const [open, setOpen] = useStoredState("help:/momentum-engine", true);
//
// The server (and the first paint) always uses the default, then the remembered value takes over, with no
// hydration error. Every read and write is inside try/catch: private windows or blocked storage simply fall
// back to the default.
import { useCallback, useSyncExternalStore } from "react";

const listeners = new Set();

// Tell every component that uses a stored value to read it again.
function notify() {
  listeners.forEach((listener) => listener());
}

// Listen for changes from this tab (notify) and from other tabs (the storage event).
function subscribe(listener) {
  listeners.add(listener);
  window.addEventListener("storage", listener);
  return () => { listeners.delete(listener); window.removeEventListener("storage", listener); };
}

// The stored text for a key, or null.
function read(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export default function useStoredState(key, defaultValue) {
  const raw = useSyncExternalStore(subscribe, () => read(key), () => null);
  let value = defaultValue;
  if (raw !== null) {
    try {
      value = JSON.parse(raw);
    } catch {
      value = defaultValue;
    }
  }
  const setValue = useCallback((next) => {
    try {
      const current = read(key);
      const old = current === null ? defaultValue : JSON.parse(current);
      window.localStorage.setItem(key, JSON.stringify(typeof next === "function" ? next(old) : next));
    } catch {
      // storage blocked: the value just is not remembered
    }
    notify();
  }, [key, defaultValue]);
  return [value, setValue];
}
