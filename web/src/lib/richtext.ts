// Texto das respostas → blocos (parágrafos e listas) com negrito, sem HTML. O modelo às vezes
// responde em markdown ("**Preço:** …", "- item"); aqui isso vira estrutura, e o React renderiza
// como elementos: nenhuma string do modelo é interpretada como HTML.

export interface Span {
  text: string;
  bold: boolean;
}

export type Block = { kind: "p"; lines: Span[][] } | { kind: "ul"; items: Span[][] };

const BULLET = /^\s*(?:[-*•]|\d+[.)])\s+/;
const HEADING = /^\s*#{1,6}\s+/;

export function parseInline(line: string): Span[] {
  const out: Span[] = [];
  const re = /\*\*(.+?)\*\*/g;
  let last = 0;
  for (let m = re.exec(line); m; m = re.exec(line)) {
    if (m.index > last) out.push({ text: line.slice(last, m.index), bold: false });
    out.push({ text: m[1] ?? "", bold: true });
    last = m.index + m[0].length;
  }
  if (last < line.length) out.push({ text: line.slice(last), bold: false });
  return out.filter((s) => s.text.length > 0);
}

export function parseRich(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of text.replace(/\r\n/g, "\n").split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      blocks.push({ kind: "p", lines: [] }); // separador de parágrafo
      continue;
    }
    if (BULLET.test(line)) {
      const item = parseInline(line.replace(BULLET, ""));
      const prev = blocks.at(-1);
      if (prev?.kind === "ul") prev.items.push(item);
      else blocks.push({ kind: "ul", items: [item] });
      continue;
    }
    // título em markdown ("# Formas de pagamento") vira linha em negrito
    const spans = HEADING.test(line)
      ? [{ text: line.replace(HEADING, ""), bold: true }]
      : parseInline(line);
    const prev = blocks.at(-1);
    if (prev?.kind === "p" && prev.lines.length) prev.lines.push(spans);
    else blocks.push({ kind: "p", lines: [spans] });
  }
  return blocks.filter((b) => (b.kind === "p" ? b.lines.length > 0 : b.items.length > 0));
}
