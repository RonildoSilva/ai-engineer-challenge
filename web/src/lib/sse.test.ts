import { describe, expect, it } from "vitest";
import { readEvents, splitEvents } from "./sse";

describe("splitEvents", () => {
  it("separa blocos completos e guarda o incompleto", () => {
    const { events, rest } = splitEvents('event: sessao\r\ndata: {"sessao":"a"}\r\n\r\nevent: assu');
    expect(events).toEqual([{ event: "sessao", data: '{"sessao":"a"}' }]);
    expect(rest).toBe("event: assu");
  });

  it("aceita LF, ignora comentários (ping) e junta várias linhas de data", () => {
    const { events } = splitEvents(": ping\n\nevent: x\ndata: a\ndata: b\n\n");
    expect(events).toEqual([{ event: "x", data: "a\nb" }]);
  });
});

describe("readEvents", () => {
  it("reconstrói eventos partidos entre chunks", async () => {
    const enc = new TextEncoder();
    const chunks = ["event: assunto\r\nda", 'ta: {"intencoes":["produto"]}\r\n', "\r\nevent: resposta\r\n", 'data: {"ok":1}\r\n\r\n'];
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (const ch of chunks) c.enqueue(enc.encode(ch));
        c.close();
      },
    });
    const got = [];
    for await (const e of readEvents(body)) got.push(e);
    expect(got.map((e) => e.event)).toEqual(["assunto", "resposta"]);
    expect(JSON.parse(got[0]!.data)).toEqual({ intencoes: ["produto"] });
  });
});
