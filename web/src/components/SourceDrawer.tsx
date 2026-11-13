import { useEffect, useState } from "react";
import { api, Row } from "../api";
import Table from "./Table";

// What to show: either the facts behind a KPI figure, or one source record.
export type DrawerRequest =
  | { kind: "kpi"; building: string; kpi: string; title: string }
  | { kind: "source"; source: string; ref: string; title?: string };

export default function SourceDrawer({ request, onClose }: { request: DrawerRequest; onClose: () => void }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [record, setRecord] = useState<{ title: string; rows: Row[] } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setRows(null);
    setRecord(null);
    const load = request.kind === "kpi" ? api.kpiFacts(request.building, request.kpi) : api.source(request.source, request.ref);
    load.then(setRows).catch((e) => setError(String(e)));
  }, [request]);

  const openRecord = (r: Row) => {
    const source = String(r.source ?? ""), ref = String(r.source_ref ?? "");
    api.source(source, ref).then((rows) => setRecord({ title: `${source} · ${ref}`, rows }));
  };

  const title = request.kind === "kpi" ? request.title : request.title ?? `${request.source} · ${request.ref}`;
  const total = request.kind === "kpi" && rows ? rows.reduce((s, r) => s + Number(r.value ?? 0), 0) : null;

  return (
    <aside className="drawer">
      <div className="drawer-head">
        <h2>{title}</h2>
        <button onClick={onClose}>Close</button>
      </div>
      {error && <p className="error">{error}</p>}
      {!rows && !error && <p>Loading…</p>}
      {rows && request.kind === "kpi" && (
        <>
          <p className="muted">
            {rows.length} source record(s){total !== null ? `, summing to ${total.toLocaleString("en-US", { maximumFractionDigits: 2 })}` : ""}.
            Click a row to open the original record.
          </p>
          <Table rows={rows} columns={["kpi", "source", "source_ref", "value", "detail"]} onRowClick={openRecord} />
        </>
      )}
      {rows && request.kind === "source" && <RecordView rows={rows} />}
      {record && (
        <div className="record">
          <h3>{record.title}</h3>
          <RecordView rows={record.rows} />
        </div>
      )}
    </aside>
  );
}

// The source record(s) as label/value pairs.
function RecordView({ rows }: { rows: Row[] }) {
  if (!rows.length) return <p className="muted">No stored record for this reference.</p>;
  return (
    <>
      {rows.map((r, i) => (
        <dl key={i} className="kv">
          {Object.entries(r).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v === null ? "–" : String(v)}</dd>
            </div>
          ))}
        </dl>
      ))}
    </>
  );
}
