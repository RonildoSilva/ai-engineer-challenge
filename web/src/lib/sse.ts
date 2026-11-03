// Leitor de text/event-stream sobre fetch (o EventSource só faz GET, e a API usa POST para que a
// mensagem do cliente não vá na URL). Tolerante a CRLF e a blocos partidos entre chunks.

export interface RawEvent {
  event: string;
  data: string;
}

/** Separa um buffer em blocos completos; o resto (bloco incompleto) volta para o buffer. */
export function splitEvents(buffer: string): { events: RawEvent[]; rest: string } {
  const blocks = buffer.split(/\r?\n\r?\n/);
  const rest = blocks.pop() ?? "";
  const events: RawEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(":")) continue; // comentário (ping do servidor)
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (data.length) events.push({ event, data: data.join("\n") });
  }
  return { events, rest };
}

export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<RawEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { events, rest } = splitEvents(buffer);
    buffer = rest;
    yield* events;
  }
  const { events } = splitEvents(buffer + "\n\n");
  yield* events;
}
