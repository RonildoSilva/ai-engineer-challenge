import { useEffect, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { Header } from "./components/Header";
import { MessageBubble } from "./components/MessageBubble";
import { ProgressBubble } from "./components/ProgressBubble";
import { TracePanel } from "./components/TracePanel";
import { Welcome } from "./components/Welcome";
import { useChat } from "./hooks/useChat";
import { useTheme } from "./hooks/useTheme";

export default function App() {
  const chat = useChat();
  const theme = useTheme();
  const [traceOpen, setTraceOpen] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  // rola para a última mensagem só quando há conversa (na boas-vindas, o topo fica visível)
  useEffect(() => {
    if (chat.messages.length || chat.busy) {
      end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [chat.messages.length, chat.progress, chat.busy]);

  return (
    <div className="mx-auto flex h-dvh max-w-7xl overflow-hidden bg-base lg:border-x lg:border-line">
      <main className="flex min-w-0 flex-1 flex-col" aria-label="Conversa com a Mel">
        <Header
          online={chat.online}
          health={chat.health}
          theme={theme.choice}
          onTheme={theme.cycle}
          onNew={chat.reset}
          onTrace={() => setTraceOpen(true)}
        />
        <section className="scroll-thin flex-1 overflow-y-auto px-4 py-6 sm:px-6" aria-live="polite">
          <div className="mx-auto flex max-w-3xl flex-col gap-5">
            {chat.messages.length === 0 && !chat.busy ? (
              <Welcome onPick={chat.send} />
            ) : (
              chat.messages.map((m) => <MessageBubble key={m.id} msg={m} />)
            )}
            {chat.busy && chat.progress && chat.startedAt && (
              <ProgressBubble progress={chat.progress} startedAt={chat.startedAt} />
            )}
            <div ref={end} />
          </div>
        </section>
        <Composer busy={chat.busy} onSend={chat.send} />
      </main>
      <TracePanel
        trace={chat.trace}
        session={chat.session}
        health={chat.health}
        open={traceOpen}
        onClose={() => setTraceOpen(false)}
      />
      {traceOpen && (
        <button
          type="button"
          aria-label="Fechar bastidores"
          onClick={() => setTraceOpen(false)}
          className="fixed inset-0 z-10 bg-black/40 lg:hidden"
        />
      )}
    </div>
  );
}
