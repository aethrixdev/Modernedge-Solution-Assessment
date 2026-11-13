import { useEffect, useState } from "react";
import { api, KpiResponse, money, pct } from "../api";
import { DrawerRequest } from "./SourceDrawer";
import Table from "./Table";

// Each column: label, how to format it. Every figure is clickable -> its source records.
const COLUMNS: [string, string, (v: unknown) => string][] = [
  ["contracted_headcount", "Contracted", String],
  ["active_headcount", "Active", String],
  ["headcount_variance", "Variance", (v) => (Number(v) > 0 ? `+${v}` : String(v))],
  ["scheduled_shifts", "Scheduled shifts", String],
  ["callout_shifts", "Call-outs", String],
  ["callout_rate", "Call-out rate", pct],
  ["fill_rate", "Fill rate", pct],
  ["overtime_hours", "OT hours", String],
  ["overtime_dollars", "OT $", money],
  ["revenue", "Revenue", money],
  ["labor_cost", "Labor cost", money],
  ["gross_margin", "Gross margin", money],
  ["gross_margin_pct", "GM %", pct],
  ["unbilled_coverage_count", "Unbilled #", String],
  ["unbilled_coverage_dollars", "Unbilled $", money],
];

export default function KpiPanel({ open }: { open: (r: DrawerRequest) => void }) {
  const [data, setData] = useState<KpiResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api.kpis().then(setData).catch((e) => setError(String(e))); }, []);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p>Loading…</p>;

  const totalRevenue = data.buildings.reduce((s, b) => s + Number(b.revenue), 0);
  const totalLabor = data.buildings.reduce((s, b) => s + Number(b.labor_cost), 0);
  const unbilled = data.buildings.reduce((s, b) => s + Number(b.unbilled_coverage_dollars), 0);
  const arTotal = data.ar_aging.reduce((s, b) => s + Number(b.open_amount), 0);

  return (
    <section>
      <div className="tiles">
        <Tile label="Revenue" value={money(totalRevenue)} />
        <Tile label="Gross margin" value={`${money(totalRevenue - totalLabor)} (${pct((totalRevenue - totalLabor) / totalRevenue)})`} />
        <Tile label="Unbilled coverage" value={money(unbilled)} />
        <Tile label="Open receivables" value={money(arTotal)} />
      </div>

      <h2>By building</h2>
      <p className="muted">Click any figure to see every source record behind it.</p>
      <div className="table-wrap">
        <table className="kpi">
          <thead>
            <tr>
              <th>Building</th>
              {COLUMNS.map(([, label]) => <th key={label}>{label}</th>)}
            </tr>
          </thead>
          <tbody>
            {data.buildings.map((b) => (
              <tr key={String(b.building)}>
                <th>{String(b.building)}</th>
                {COLUMNS.map(([key, label, fmt]) => (
                  <td key={key}>
                    <button className="link" onClick={() =>
                      open({ kind: "kpi", building: String(b.building), kpi: key, title: `${b.building} · ${label}` })}>
                      {fmt(b[key])}
                    </button>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Accounts receivable aging (as of period end, both entities)</h2>
      <div className="tiles">
        {data.ar_aging.map((a) => <Tile key={String(a.bucket)} label={String(a.bucket).replace("_", "–")} value={money(a.open_amount)} />)}
      </div>
      <details>
        <summary>Open items ({data.ar_items.length})</summary>
        <Table rows={data.ar_items} columns={["entity", "doc_no", "doc_type", "customer", "open_amount", "aged_from", "days_past_due", "bucket"]} />
      </details>
    </section>
  );
}

// One headline number at the top of the KPI panel.
function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
    </div>
  );
}
