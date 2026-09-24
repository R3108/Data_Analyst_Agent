"use client";

import { useEffect, useState } from "react";

/**
 * The modifier key to print in shortcut hints: "⌘" on Apple platforms, "Ctrl" elsewhere.
 * Starts as "Ctrl" so the server render and first client render agree.
 */
export function useModKey(): "⌘" | "Ctrl" {
  const [key, setKey] = useState<"⌘" | "Ctrl">("Ctrl");
  useEffect(() => {
    if (/Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent)) setKey("⌘");
  }, []);
  return key;
}
