import { useEffect, useState } from "react";
import { api, Row } from "../api";
import { DrawerRequest } from "./SourceDrawer";

const CODES = ["SKP", "SKU", "LTN", "CLN", "BRV", "JRY", "FEM", "PER"];

// Absences the pipeline would not write on its own. A reviewer reads the original
// message, then approves (with a code) or rejects. Approval uses the same safe payroll writer.
export default function ReviewQueue({ open }: { open: (r: DrawerRequest) => void }) {
  const [items, setItems] = useState<Row[] | null>(null);
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [result, setResult] = useState<Record<string, string>>({});

  const load = () => api.reviewQueue().then(setItems);
  useEffect(() => { load(); }, []);

  const decide = async (r: Row, action: "approve" | "reject") => {
    const id = String(r.absence_id);
    const out = await api.decide(id, action, codes[id] ?? String(r.code ?? ""));
    setResult({ ...result, [id]: JSON.stringify(out.final_status ?? out.error ?? out.decision ?? action) });
    load();
  };

  if (!items) return <p>Loading…</p>;
  if (!items.length) return <p className="muted">Nothing is waiting for review.</p>;
  return (
    <section>
      <p className="muted">{items.length} item(s) held. Nothing here has been written to payroll.</p>
      {items.map((r) => {
        const id = String(r.absence_id);
        return (
          <article key={id} className="card">
            <header>
              <strong>{String(r.employee ?? "Unknown employee")}</strong>
              <span>{r.date ? String(r.date).slice(0, 10) : "date unknown"} · {String(r.shift ?? "")} · {String(r.building ?? "")}</span>
            </header>
            <p className="reason">Held because: {String(r.review_reasons)}</p>
            {r.message && <blockquote>{String(r.message)}</blockquote>}
            <p className="muted">
              {String(r.channel ?? "")} logged {String(r.logged_at ?? "")}
              {r.vm_received_at ? ` · voicemail received ${r.vm_received_at}` : " · no voicemail timestamp"}
              {r.notice_hours != null ? ` · notice ${r.notice_hours}h` : ""} ·{" "}
              <button className="link" onClick={() => open({ kind: "source", source: String(r.source), ref: String(r.source_ref) })}>
                {String(r.source)} {String(r.source_ref)}
              </button>
            </p>
            <div className="actions">
              <label>
                Code{" "}
                <select value={codes[id] ?? String(r.code ?? "")} onChange={(e) => setCodes({ ...codes, [id]: e.target.value })}>
                  <option value="">–</option>
                  {CODES.map((c) => <option key={c}>{c}</option>)}
                </select>
              </label>
              {r.code && <span className="muted">proposed: {String(r.code)} ({String(r.code_reason ?? "")})</span>}
              <button onClick={() => decide(r, "approve")} disabled={!(codes[id] ?? r.code) || !r.payroll_id}>Approve & write</button>
              <button onClick={() => decide(r, "reject")}>Reject</button>
              {result[id] && <span className="muted">→ {result[id]}</span>}
            </div>
          </article>
        );
      })}
    </section>
  );
}
