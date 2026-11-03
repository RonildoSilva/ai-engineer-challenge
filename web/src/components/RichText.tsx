import { Fragment } from "react";
import { parseRich, type Span } from "../lib/richtext";

function Inline({ spans }: { spans: Span[] }) {
  return (
    <>
      {spans.map((s, i) =>
        s.bold ? (
          <strong key={i} className="font-semibold">
            {s.text}
          </strong>
        ) : (
          <Fragment key={i}>{s.text}</Fragment>
        ),
      )}
    </>
  );
}

/** Parágrafos, listas e negrito a partir do texto do modelo, sempre como elementos (nunca HTML). */
export function RichText({ text }: { text: string }) {
  return (
    <div className="space-y-2">
      {parseRich(text).map((b, i) =>
        b.kind === "ul" ? (
          <ul key={i} className="ml-1 space-y-1">
            {b.items.map((item, j) => (
              <li key={j} className="flex gap-2">
                <span className="mt-[0.55em] size-1.5 shrink-0 rounded-full bg-accent" />
                <span>
                  <Inline spans={item} />
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p key={i}>
            {b.lines.map((line, j) => (
              <Fragment key={j}>
                {j > 0 && <br />}
                <Inline spans={line} />
              </Fragment>
            ))}
          </p>
        ),
      )}
    </div>
  );
}
