import type { StreamEvent } from "./types";

// Etapas mostradas enquanto a Mel trabalha (o modelo local leva até ~2 min por resposta).
export type Stage = "recebida" | "entendendo" | "consultando" | "redigindo" | "revisando";

export const STAGES: Array<{ id: Stage; label: string }> = [
  { id: "entendendo", label: "Entendendo" },
  { id: "consultando", label: "Consultando a loja" },
  { id: "redigindo", label: "Escrevendo" },
];

export const TOOL_LABEL: Record<string, string> = {
  buscar_produtos: "catálogo",
  detalhar_produto: "produto",
  consultar_pedido: "pedido",
  verificar_devolucao: "regras de devolução",
  simular_pagamento: "pagamento",
  calcular_frete: "frete",
  status_loja: "horário",
  consultar_politica: "manual da loja",
};

export const INTENT_LABEL: Record<string, string> = {
  produto: "produto",
  catalogo: "catálogo",
  acessorio: "acessório",
  pedido: "pedido",
  devolucao: "troca e devolução",
  pagamento: "pagamento",
  frete: "frete",
  horario: "horário",
  politica: "informações da loja",
  reclamacao: "atendimento",
  saudacao: "conversa",
  geral: "assunto geral",
};

export interface Progress {
  stage: Stage;
  detail: string;
}

/** Evento do stream → etapa e texto curto para o usuário (null = não muda o progresso). */
export function progressFor(ev: StreamEvent): Progress | null {
  switch (ev.event) {
    case "sessao":
      return { stage: "recebida", detail: "Mensagem recebida" };
    case "bloqueio":
      return { stage: "redigindo", detail: "Respondendo" };
    case "assunto": {
      const what = ev.data.intencoes.map((i) => INTENT_LABEL[i] ?? i).join(" e ");
      return { stage: "entendendo", detail: `Entendi: ${what}${ev.data.herdado ? " (continuação)" : ""}` };
    }
    case "dados": {
      if (!ev.data.tools.length) return { stage: "consultando", detail: "Preparando a resposta" };
      const what = ev.data.tools.map((t) => TOOL_LABEL[t] ?? t).join(", ");
      return { stage: "consultando", detail: `Consultei ${what}` };
    }
    case "redigindo":
      return { stage: "redigindo", detail: "Escrevendo a resposta" };
    case "tool":
      return { stage: "consultando", detail: `Consultando ${TOOL_LABEL[ev.data.nome] ?? ev.data.nome}` };
    case "correcao":
      return { stage: "revisando", detail: "Revisando a resposta" };
    default:
      return null;
  }
}
