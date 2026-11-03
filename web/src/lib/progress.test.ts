import { describe, expect, it } from "vitest";
import { progressFor } from "./progress";

describe("progressFor", () => {
  it("traduz os eventos da API em etapas legíveis", () => {
    expect(progressFor({ event: "assunto", data: { intencoes: ["produto", "pagamento"], herdado: false } })).toEqual({
      stage: "entendendo",
      detail: "Entendi: produto e pagamento",
    });
    expect(progressFor({ event: "dados", data: { tools: ["detalhar_produto", "simular_pagamento"], resposta_pronta: false } })?.detail).toBe(
      "Consultei produto, pagamento",
    );
    expect(progressFor({ event: "redigindo", data: { chamada: 1, prompt_tokens: 900 } })?.stage).toBe("redigindo");
  });

  it("a resposta final não é etapa de progresso", () => {
    expect(progressFor({ event: "erro", data: { erro: "x" } })).toBeNull();
  });
});
