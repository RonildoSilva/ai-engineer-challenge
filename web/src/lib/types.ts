// Contrato da API REST (src/emporio/api/app.py). Nomes em português, como na API.

export interface TurnPayload {
  sessao: string;
  resposta: string;
  request_id: string;
  assunto: string[];
  pre_busca: string[];
  tools: string[];
  chamadas_llm: number;
  prompt_tokens: number;
  latencia_s: number;
  resposta_pronta: boolean;
  bloqueio: string | null;
  encaminhado: boolean;
  resposta_de_seguranca: string | null;
}

export interface Health {
  status: "ok" | "degradado";
  perfil: string;
  modelo: string;
  llm: "ok" | "fora do ar";
}

export interface StoredMessage {
  role: "user" | "assistant";
  content: string;
}

export interface SessionHistory {
  sessao: string;
  mensagens: StoredMessage[];
  turnos: Array<Record<string, unknown>>;
}

/** Eventos do POST /chat/stream (Server-Sent Events). */
export type StreamEvent =
  | { event: "sessao"; data: { sessao: string } }
  | { event: "assunto"; data: { intencoes: string[]; herdado: boolean } }
  | { event: "dados"; data: { tools: string[]; resposta_pronta: boolean } }
  | { event: "redigindo"; data: { chamada: number; prompt_tokens: number } }
  | { event: "tool"; data: { nome: string } }
  | { event: "correcao"; data: { motivo: string } }
  | { event: "bloqueio"; data: { motivo: string } }
  | { event: "resposta"; data: TurnPayload }
  | { event: "erro"; data: { erro: string } };
