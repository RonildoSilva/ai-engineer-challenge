import { Monitor, Moon, PanelRight, SquarePen, Sun } from "lucide-react";
import type { ThemeChoice } from "../hooks/useTheme";
import type { Health } from "../lib/types";
import { BrandMark } from "./BrandMark";

const THEME = {
  system: { icon: Monitor, label: "Tema do sistema" },
  light: { icon: Sun, label: "Tema claro" },
  dark: { icon: Moon, label: "Tema escuro" },
} as const;

interface Props {
  online: boolean | null;
  health: Health | null;
  theme: ThemeChoice;
  onTheme: () => void;
  onNew: () => void;
  onTrace: () => void;
}

function IconButton(props: { label: string; onClick: () => void; children: React.ReactNode; className?: string }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      title={props.label}
      aria-label={props.label}
      className={`grid size-9 place-items-center rounded-full border border-line text-muted transition hover:border-accent hover:text-ink ${props.className ?? ""}`}
    >
      {props.children}
    </button>
  );
}

export function Header({ online, health, theme, onTheme, onNew, onTrace }: Props) {
  const ThemeIcon = THEME[theme].icon;
  const status =
    online === null
      ? { dot: "bg-muted", text: "conectando…" }
      : online && health?.llm === "ok"
        ? { dot: "bg-ok", text: "online" }
        : online
          ? { dot: "bg-warn", text: "modelo fora do ar" }
          : { dot: "bg-bad", text: "API fora do ar" };
  return (
    <header className="flex items-center gap-3 border-b border-line bg-surface/80 px-4 py-3 backdrop-blur sm:px-6">
      <BrandMark />
      <div className="min-w-0">
        <h1 className="font-serif text-lg leading-tight sm:text-xl">
          Empório <span className="gold-text">da Música</span>
        </h1>
        <p className="flex items-center gap-1.5 text-xs text-muted">
          <span className={`size-2 rounded-full ${status.dot}`} aria-hidden="true" />
          Mel<span className="hidden sm:inline"> · assistente virtual</span> · {status.text}
        </p>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <IconButton label="Nova conversa" onClick={onNew}>
          <SquarePen size={16} />
        </IconButton>
        <IconButton label={`${THEME[theme].label} (alternar)`} onClick={onTheme}>
          <ThemeIcon size={16} />
        </IconButton>
        <IconButton label="Bastidores" onClick={onTrace} className="lg:hidden">
          <PanelRight size={16} />
        </IconButton>
      </div>
    </header>
  );
}
