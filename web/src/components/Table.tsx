import type { ReactNode } from "react";
import { Row } from "../api";

// A plain table. Columns default to the keys of the first row.
export default function Table({ rows, columns, onRowClick, render }: {
  rows: Row[];
  columns?: string[];
  onRowClick?: (r: Row) => void;
  render?: Partial<Record<string, (r: Row) => ReactNode>>;
}) {
  if (!rows.length) return <p className="muted">Nothing to show.</p>;
  const cols = columns ?? Object.keys(rows[0]);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{cols.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} onClick={onRowClick ? () => onRowClick(r) : undefined} className={onRowClick ? "clickable" : ""}>
              {cols.map((c) => (
                <td key={c}>{render?.[c] ? render[c]!(r) : r[c] === null || r[c] === undefined ? "–" : String(r[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
