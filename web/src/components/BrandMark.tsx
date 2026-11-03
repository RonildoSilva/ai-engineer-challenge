import { Guitar } from "lucide-react";

export function BrandMark({ size = 44 }: { size?: number }) {
  return (
    <div
      className="gold-gradient grid shrink-0 place-items-center rounded-2xl shadow-soft ring-1 ring-black/5"
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <Guitar className="text-[#1b1409]" size={size * 0.52} strokeWidth={1.9} />
    </div>
  );
}
