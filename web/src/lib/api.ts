import { readEvents } from "./sse";
import type { Health, SessionHistory, StreamEvent } from "./types";

declare global {
  interface Window {
    EMPORIO_API?: string;
  }
}

export const API_URL = (
  new URLSearchParams(window.location.search).get("api") ??
  window.EMPORIO_API ??
  "http://127.0.0.1:8010"
).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export async function getHealth(): Promise<Health> {
  const r = await fetch(`${API_URL}/health`);
  if (!r.ok) throw new ApiError(`health ${r.status}`, r.status);
  return (await r.json()) as Health;
}

export async function getSession(id: string): Promise<SessionHistory | null> {
  const r = await fetch(`${API_URL}/sessions/${encodeURIComponent(id)}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new ApiError(`sessão ${r.status}`, r.status);
  return (await r.json()) as SessionHistory;
}

/** Envia a mensagem e entrega os eventos do turno conforme chegam. */
export async function* streamChat(
  mensagem: string,
  sessao: string | null,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const r = await fetch(`${API_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sessao ? { mensagem, sessao } : { mensagem }),
    signal,
  });
  if (!r.ok || !r.body) {
    const body = (await r.json().catch(() => ({}))) as { erro?: string };
    throw new ApiError(body.erro ?? `erro ${r.status}`, r.status);
  }
  for await (const raw of readEvents(r.body)) {
    yield { event: raw.event, data: JSON.parse(raw.data) } as StreamEvent;
  }
}
