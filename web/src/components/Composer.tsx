import { SendHorizontal } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

const MAX = 2000;

export function Composer({ busy, onSend }: { busy: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  useEffect(() => {
    if (!busy) ref.current?.focus();
  }, [busy]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!text.trim() || busy) return;
    onSend(text);
    setText("");
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form onSubmit={submit} className="border-t border-line bg-surface/80 px-4 py-3 backdrop-blur sm:px-6">
      <div className="flex items-end gap-2 rounded-3xl border border-line bg-base px-2 py-1.5 transition-colors focus-within:border-accent">
        <label htmlFor="mensagem" className="sr-only">
          Mensagem
        </label>
        <textarea
          id="mensagem"
          ref={ref}
          rows={1}
          maxLength={MAX}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          disabled={busy}
          placeholder={busy ? "A Mel está respondendo…" : "Escreva sua mensagem…"}
          className="max-h-40 flex-1 resize-none bg-transparent px-3 py-2 text-[0.95rem] outline-none placeholder:text-muted disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          aria-label="Enviar"
          className="gold-gradient grid size-10 shrink-0 place-items-center rounded-full text-[#1b1409] shadow-soft transition hover:brightness-105 active:scale-95 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <SendHorizontal size={18} />
        </button>
      </div>
      <p className="mt-2 text-center text-[0.72rem] text-muted">
        Modelo local em CPU: respostas que consultam o modelo levam de 1 a 2 minutos. Horário,
        endereço e pedidos sem dados respondem na hora.
      </p>
    </form>
  );
}
