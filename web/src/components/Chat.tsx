import { useState } from "react";
import { api, ChatResponse } from "../api";
import { DrawerRequest } from "./SourceDrawer";

type Turn = { role: "user" | "assistant"; content: string; meta?: ChatResponse };

const EXAMPLES = [
  "Which building had the most call-outs, and why?",
  "Which coverage shifts are unbilled, and how much are they worth?",
  "Which building has the lowest gross margin percentage?",
  "Who is waiting in the review queue, and why?",
];

// Answers come only from the database; every [source | ref] tag is a link to the record.
export default function Chat({ open }: { open: (r: DrawerRequest) => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  const ask = async (question: string) => {
    if (!question.trim()) return;
    const history = turns.map(({ role, content }) => ({ role, content }));
    setTurns((t) => [...t, { role: "user", content: question }]);
    setQ("");
    setBusy(true);
    try {
      const res = await api.chat(question, history);
      setTurns((t) => [...t, { role: "assistant", content: res.answer, meta: res }]);
    } catch (e) {
      setTurns((t) => [...t, { role: "assistant", content: `Error: ${e}` }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="chat">
      {turns.length === 0 && (
        <div className="examples">
          {EXAMPLES.map((e) => <button key={e} onClick={() => ask(e)}>{e}</button>)}
        </div>
      )}
      {turns.map((t, i) => (
        <div key={i} className={`turn ${t.role}`}>
          <Linked text={t.content} open={open} />
          {t.meta && t.meta.queries.length > 0 && (
            <details>
              <summary>How this was looked up ({t.meta.queries.length} quer{t.meta.queries.length === 1 ? "y" : "ies"})</summary>
              {t.meta.queries.map((x, j) => <pre key={j}>{x.sql}</pre>)}
            </details>
          )}
        </div>
      ))}
      {busy && <p className="muted">Looking it up…</p>}
      <form onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about buildings, staff, call-outs, coverage, revenue…" />
        <button disabled={busy}>Ask</button>
      </form>
    </section>
  );
}

// Turn "[reception_log | 14]" into a clickable link to that source record.
function Linked({ text, open }: { text: string; open: (r: DrawerRequest) => void }) {
  const parts = text.split(/(\[[^\]|]+\|[^\]]+\])/g);
  return (
    <p className="answer">
      {parts.map((p, i) => {
        const m = p.match(/^\[([^\]|]+)\|([^\]]+)\]$/);
        if (!m) return <span key={i}>{p}</span>;
        const source = m[1].trim(), ref = m[2].trim();
        return <button key={i} className="cite" onClick={() => open({ kind: "source", source, ref })}>{source} {ref}</button>;
      })}
    </p>
  );
}
