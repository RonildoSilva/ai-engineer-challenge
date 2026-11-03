import { CircleCheck, TriangleAlert, Zap } from "lucide-react";
import type { ChatMessage } from "../hooks/useChat";
import { BrandMark } from "./BrandMark";
import { RichText } from "./RichText";

function Meta({ msg }: { msg: ChatMessage }) {
  const t = msg.turn;
  if (!t) return null;
  if (t.resposta_de_seguranca)
    return (
      <span className="flex items-center gap-1 text-warn">
        <TriangleAlert size={12} /> encaminhada à equipe por segurança
      </span>
    );
  if (t.resposta_pronta)
    return (
      <span className="flex items-center gap-1">
        <Zap size={12} /> resposta imediata
      </span>
    );
  return (
    <span className="flex items-center gap-1">
      <CircleCheck size={12} /> {Math.round(t.latencia_s)} s · consultado na loja
    </span>
  );
}

export function MessageBubble({ msg }: { msg: ChatMessage }) {
  if (msg.role === "user") {
    return (
      <div className="animate-rise flex justify-end">
        <div className="gold-gradient max-w-[min(80%,36rem)] rounded-2xl rounded-br-md px-4 py-2.5 text-[0.95rem] leading-relaxed text-bubble-user-ink shadow-soft whitespace-pre-wrap break-words">
          {msg.text}
        </div>
      </div>
    );
  }
  return (
    <div className="animate-rise flex items-end gap-2.5">
      <BrandMark size={30} />
      <div className="max-w-[min(82%,40rem)]">
        <div
          className={`rounded-2xl rounded-bl-md border bg-surface px-4 py-3 text-[0.95rem] leading-relaxed shadow-soft break-words ${
            msg.error ? "border-bad/40 text-bad" : "border-line"
          }`}
        >
          <RichText text={msg.text} />
        </div>
        <div className="mt-1 pl-1 text-xs text-muted">
          <Meta msg={msg} />
        </div>
      </div>
    </div>
  );
}
