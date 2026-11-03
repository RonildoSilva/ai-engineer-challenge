import { useEffect, useState } from "react";
import { type Progress, STAGES } from "../lib/progress";
import { BrandMark } from "./BrandMark";

/** "Digitando…" com a etapa atual e o tempo corrido (o modelo local leva até ~2 min). */
export function ProgressBubble({ progress, startedAt }: { progress: Progress; startedAt: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const seconds = Math.max(0, Math.round((now - startedAt) / 1000));
  const current = STAGES.findIndex((s) => s.id === progress.stage);

  return (
    <div className="animate-rise flex items-end gap-2.5" role="status" aria-live="polite">
      <BrandMark size={30} />
      <div className="rounded-2xl rounded-bl-md border border-line bg-surface px-4 py-3 shadow-soft">
        <div className="flex items-center gap-3">
          <span className="flex gap-1" aria-hidden="true">
            <span className="typing-dot size-1.5 rounded-full bg-accent" />
            <span className="typing-dot size-1.5 rounded-full bg-accent" />
            <span className="typing-dot size-1.5 rounded-full bg-accent" />
          </span>
          <span className="text-sm">{progress.detail}</span>
          {seconds >= 3 && <span className="text-xs tabular-nums text-muted">{seconds} s</span>}
        </div>
        <ol className="mt-2.5 flex items-center gap-1.5 text-[0.7rem] text-muted">
          {STAGES.map((s, i) => (
            <li key={s.id} className="flex items-center gap-1.5">
              {i > 0 && <span className="h-px w-3 bg-line" />}
              <span
                className={`rounded-full px-2 py-0.5 ${
                  i < current || (progress.stage === "revisando" && i <= 2)
                    ? "bg-accent-soft text-accent"
                    : i === current
                      ? "bg-accent text-surface"
                      : "bg-surface-2"
                }`}
              >
                {s.label}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
