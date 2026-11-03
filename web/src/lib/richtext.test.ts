import { describe, expect, it } from "vitest";
import { parseInline, parseRich } from "./richtext";

describe("parseRich", () => {
  it("negrito vira span, nunca HTML", () => {
    expect(parseInline("**Preço:** R$ 2.199,00 <script>")).toEqual([
      { text: "Preço:", bold: true },
      { text: " R$ 2.199,00 <script>", bold: false },
    ]);
  });

  it("agrupa listas e parágrafos", () => {
    const blocks = parseRich("Opções:\n- Tagima: R$ 429,90\n- Rozini: R$ 459,90\n\nQuer ajuda?");
    expect(blocks.map((b) => b.kind)).toEqual(["p", "ul", "p"]);
    expect(blocks[1]).toMatchObject({ kind: "ul", items: [[{ text: "Tagima: R$ 429,90" }], [{ text: "Rozini: R$ 459,90" }]] });
  });

  it("título de markdown vira linha em negrito; listas numeradas também", () => {
    const blocks = parseRich("# Formas de pagamento\n1. PIX\n2) Cartão");
    expect(blocks[0]).toEqual({ kind: "p", lines: [[{ text: "Formas de pagamento", bold: true }]] });
    expect(blocks[1]).toMatchObject({ kind: "ul", items: [[{ text: "PIX" }], [{ text: "Cartão" }]] });
  });
});
