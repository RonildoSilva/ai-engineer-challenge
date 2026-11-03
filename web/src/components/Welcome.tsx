import { Clock, MapPin, Package, Search, Undo2 } from "lucide-react";
import type { ComponentType } from "react";
import { BrandMark } from "./BrandMark";

const SUGGESTIONS: Array<{ icon: ComponentType<{ size?: number }>; text: string }> = [
  { icon: Search, text: "Quais violões vocês têm até R$1000?" },
  { icon: Package, text: "Quanto custa o Takamine GD20?" },
  { icon: Undo2, text: "Posso devolver um produto?" },
  { icon: Clock, text: "Vocês abrem no domingo?" },
  { icon: MapPin, text: "Qual o endereço da loja?" },
];

export function Welcome({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="animate-rise mx-auto flex max-w-xl flex-col items-center py-8 text-center sm:py-14">
      <BrandMark size={64} />
      <h2 className="mt-5 font-serif text-3xl tracking-tight sm:text-4xl">
        Oi! Eu sou a <span className="gold-text">Mel</span>.
      </h2>
      <p className="mt-3 text-muted">
        Assistente virtual da Empório da Música. Posso ajudar com instrumentos e preços, pedidos,
        trocas e devoluções, pagamento, frete e horário da loja.
      </p>
      <div className="mt-8 grid w-full gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map(({ icon: Icon, text }) => (
          <button
            key={text}
            type="button"
            onClick={() => onPick(text)}
            className="group flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-left text-sm shadow-soft transition hover:-translate-y-0.5 hover:border-accent"
          >
            <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent">
              <Icon size={16} />
            </span>
            <span>{text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
