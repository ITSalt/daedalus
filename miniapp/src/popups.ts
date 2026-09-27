// Whether this device raises notification pop-ups inside the app.
//
// Kept per device, not with the host's notification preferences: the operator works in the desktop
// window, where a pop-up in the corner sits over the controls being used, and reads the same
// installation on a phone, where a banner is the only sign that something happened. One account-wide
// switch would force the same answer on both. It governs only the transient pop-ups: the inbox, the
// bell's count, push, desktop and Telegram notifications are the host's business and are untouched.

import { useSyncExternalStore } from "react";

const KEY = "daedalus.notice.popups";

/** What this page believes when storage cannot be read or written (a private window, blocked site
 *  data): the switch still works for as long as the page is open. */
let fallback = true;
const listeners = new Set<() => void>();

export function popupsShown(): boolean {
  try {
    const stored = localStorage.getItem(KEY);
    return stored === null ? fallback : stored !== "off";
  } catch {
    return fallback;
  }
}

export function setPopupsShown(on: boolean): void {
  fallback = on;
  try {
    if (on) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, "off");
  } catch {
    /* storage refused: the page remembers it until it is closed */
  }
  for (const listener of [...listeners]) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another window of the same app on this device flips the same switch.
  const onStorage = (e: StorageEvent) => {
    if (e.key === null || e.key === KEY) listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function usePopupsShown(): boolean {
  return useSyncExternalStore(subscribe, popupsShown, () => true);
}
