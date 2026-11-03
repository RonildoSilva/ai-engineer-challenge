import { Activity, Cpu, Database, Hash, ShieldAlert, Timer, X, Zap } from "lucide-react";
import type { TraceEntry } from "../hooks/useChat";
import { API_URL } from "../lib/api";
import { INTENT_LABEL } from "../lib/progress";
import type { Health } from "../lib/types";

function Badge({ children, tone = "default" }: { children: React.ReactNode; tone?: "default" | "ok" | "warn" | "bad" }) {
  const tones = {
    default: "bg-surface-2 text-ink",
    ok: "bg-ok/10 text-ok",
    warn: "bg-warn/10 text-warn",
    bad: "bg-bad/10 text-bad",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[0.7rem] ${tones[tone]}`}>
      {children}
    </span>
  );
}

function Turn({ entry, index }: { entry: TraceEntry; index: number }) {
  const t = entry.turn;
  return (
    <li className="animate-rise rounded-xl border border-line bg-surface p-3 shadow-soft">
      <div className="mb-2 flex items-start justify-between gap-2">
        <p className="text-sm font-medium leading-snug">{entry.question}</p>
        <span className="text-[0.7rem] text-muted">#{index}</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {t.assunto.map((a) => (
          <Badge key={a}>{INTENT_LABEL[a] ?? a}</Badge>
        ))}
        {t.pre_busca.map((p) => (
          <Badge key={`p-${p}`}>
            <Database size={10} /> {p}
          </Badge>
        ))}
        {t.tools.map((p) => (
          <Badge key={`t-${p}`}>
            <Cpu size={10} /> {p}
          </Badge>
        ))}
        {t.bloqueio && (
          <Badge tone="bad">
            <ShieldAlert size={10} /> {t.bloqueio}
          </Badge>
        )}
        {t.encaminhado && <Badge tone="warn">encaminhado à equipe</Badge>}
        {t.resposta_de_seguranca && <Badge tone="warn">resposta de segurança</Badge>}
      </div>
      <dl className="mt-2.5 grid grid-cols-3 gap-2 border-t border-line pt-2.5 text-[0.7rem]">
        <div>
          <dt className="text-muted">LLM</dt>
          <dd className="flex items-center gap-1 font-medium">
            {t.resposta_pronta ? (
              <>
                <Zap size={11} className="text-ok" /> pronta
              </>
            ) : (
              `${t.chamadas_llm}×`
            )}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Tokens</dt>
          <dd className="font-medium tabular-nums">{t.resposta_pronta ? "—" : `~${t.prompt_tokens}`}</dd>
        </div>
        <div>
          <dt className="text-muted">Tempo</dt>
          <dd className="flex items-center gap-1 font-medium tabular-nums">
            <Timer size={11} /> {t.latencia_s.toFixed(1)} s
          </dd>
        </div>
      </dl>
      {t.resposta_de_seguranca && <p className="mt-2 text-[0.7rem] text-warn">{t.resposta_de_seguranca}</p>}
      <p className="mt-2 flex items-center gap-1 font-mono text-[0.65rem] text-muted">
        <Hash size={10} /> {t.request_id}
      </p>
    </li>
  );
}

interface Props {
  trace: TraceEntry[];
  session: string | null;
  health: Health | null;
  open: boolean;
  onClose: () => void;
}

export function TracePanel({ trace, session, health, open, onClose }: Props) {
  return (
    <aside
      aria-label="Bastidores do atendimento"
      className={`scroll-thin fixed inset-x-0 bottom-0 z-20 max-h-[75dvh] overflow-y-auto rounded-t-3xl border-t border-line bg-base p-5 shadow-soft transition-transform duration-300 lg:static lg:z-auto lg:max-h-none lg:w-[22rem] lg:translate-y-0 lg:rounded-none lg:border-l lg:border-t-0 lg:shadow-none ${
        open ? "translate-y-0" : "translate-y-full"
      }`}
    >
      <div className="mb-1 flex items-center justify-between">
        <h2 className="flex items-center gap-2 font-serif text-lg">
          <Activity size={18} className="text-accent" /> Bastidores
        </h2>
        <button type="button" onClick={onClose} aria-label="Fechar bastidores" className="rounded-full p-1.5 text-muted hover:bg-surface-2 lg:hidden">
          <X size={18} />
        </button>
      </div>
      <p className="mb-4 text-xs leading-relaxed text-muted">
        O que o agente fez em cada resposta: assunto detectado, dados consultados na loja (MCP),
        chamadas ao modelo, tokens e guardrails.
      </p>
      <dl className="mb-5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 rounded-xl border border-line bg-surface p-3 text-xs">
        <dt className="text-muted">Sessão</dt>
        <dd className="font-mono">{session ?? "nova"}</dd>
        <dt className="text-muted">Modelo</dt>
        <dd>{health ? `${health.modelo} · perfil ${health.perfil}` : "—"}</dd>
        <dt className="text-muted">API</dt>
        <dd className="truncate font-mono">{API_URL}</dd>
      </dl>
      {trace.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line p-4 text-center text-xs text-muted">
          As respostas desta conversa aparecem aqui.
        </p>
      ) : (
        <ol className="space-y-3">
          {trace.map((e, i) => (
            <Turn key={e.turn.request_id} entry={e} index={trace.length - i} />
          ))}
        </ol>
      )}
    </aside>
  );
}
