import { useCallback, useEffect, useState } from "react";
import { storage } from "../lib/storage";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "emporio.tema";

function systemTheme(): "light" | "dark" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Claro, escuro ou o do sistema (padrão). O index.html aplica antes do primeiro paint. */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(() => {
    const saved = storage.get(KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  });

  useEffect(() => {
    const apply = () => {
      document.documentElement.dataset.theme = choice === "system" ? systemTheme() : choice;
    };
    apply();
    if (choice !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [choice]);

  const cycle = useCallback(() => {
    setChoice((c) => {
      const next: ThemeChoice = c === "system" ? "light" : c === "light" ? "dark" : "system";
      if (next === "system") storage.remove(KEY);
      else storage.set(KEY, next);
      return next;
    });
  }, []);

  return { choice, cycle };
}
