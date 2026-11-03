import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getHealth, getSession, streamChat } from "../lib/api";
import { type Progress, progressFor } from "../lib/progress";
import { storage } from "../lib/storage";
import type { Health, TurnPayload } from "../lib/types";

const SESSION_KEY = "emporio.sessao";

export interface ChatMessage {
  id: string;
  role: "user" | "mel";
  text: string;
  turn?: TurnPayload; // rastro da resposta (só nas da Mel desta aba)
  error?: boolean;
}

export interface TraceEntry {
  question: string;
  turn: TurnPayload;
}

let seq = 0;
const nextId = () => `m${++seq}`;

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [session, setSession] = useState<string | null>(() => storage.get(SESSION_KEY));
  const [health, setHealth] = useState<Health | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);

  // saúde da API e retomada da conversa salva no navegador
  useEffect(() => {
    let alive = true;
    getHealth()
      .then((h) => alive && (setHealth(h), setOnline(true)))
      .catch(() => alive && setOnline(false));
    const saved = storage.get(SESSION_KEY);
    if (saved) {
      getSession(saved)
        .then((s) => {
          if (!alive) return;
          if (!s) {
            storage.remove(SESSION_KEY);
            setSession(null);
            return;
          }
          setMessages(
            s.mensagens.map((m) => ({
              id: nextId(),
              role: m.role === "user" ? "user" : "mel",
              text: m.content,
            })),
          );
        })
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
  }, []);

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || busy) return;
      setMessages((m) => [...m, { id: nextId(), role: "user", text: content }]);
      setBusy(true);
      setStartedAt(Date.now());
      setProgress({ stage: "recebida", detail: "Enviando" });
      abort.current = new AbortController();
      try {
        for await (const ev of streamChat(content, session, abort.current.signal)) {
          if (ev.event === "sessao") {
            setSession(ev.data.sessao);
            storage.set(SESSION_KEY, ev.data.sessao);
          } else if (ev.event === "resposta") {
            const turn = ev.data;
            setMessages((m) => [...m, { id: nextId(), role: "mel", text: turn.resposta, turn }]);
            setTrace((t) => [{ question: content, turn }, ...t]);
          } else if (ev.event === "erro") {
            throw new ApiError(ev.data.erro, 500);
          } else {
            const p = progressFor(ev);
            if (p) setProgress(p);
          }
        }
        setOnline(true);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          storage.remove(SESSION_KEY);
          setSession(null);
        }
        const offline = !(e instanceof ApiError);
        if (offline) setOnline(false);
        setMessages((m) => [
          ...m,
          {
            id: nextId(),
            role: "mel",
            error: true,
            text: offline
              ? "Não consegui falar com o atendimento agora. Verifique se a API está no ar e tente de novo."
              : `Não consegui concluir sua mensagem (${(e as Error).message}). Tente de novo em instantes.`,
          },
        ]);
      } finally {
        setBusy(false);
        setProgress(null);
        setStartedAt(null);
        abort.current = null;
      }
    },
    [busy, session],
  );

  const reset = useCallback(() => {
    abort.current?.abort();
    storage.remove(SESSION_KEY);
    setSession(null);
    setMessages([]);
    setTrace([]);
  }, []);

  return { messages, trace, session, health, online, busy, progress, startedAt, send, reset };
}
