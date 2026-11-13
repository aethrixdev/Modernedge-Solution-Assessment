import { useEffect, useState } from "react";
import { api, Row } from "../api";
import { DrawerRequest } from "./SourceDrawer";
import Table from "./Table";

// What was sent to payroll (one row per absence), the audit trail of every call, and exceptions.
export default function PayrollLog({ open }: { open: (r: DrawerRequest) => void }) {
  const [subs, setSubs] = useState<Row[]>([]);
  const [audit, setAudit] = useState<Row[]>([]);
  const [exceptions, setExceptions] = useState<Row[]>([]);
  useEffect(() => {
    api.payroll().then((p) => { setSubs(p.submissions); setAudit(p.audit); });
    api.exceptions().then(setExceptions);
  }, []);

  const toSource = (r: Row) => open({ kind: "source", source: String(r.source), ref: String(r.source_ref ?? r.record_ref) });
  return (
    <section>
      <h2>Payroll decisions ({subs.length})</h2>
      <Table rows={subs} onRowClick={toSource} />
      <h2>Exceptions ({exceptions.length})</h2>
      <Table rows={exceptions} onRowClick={(r) => open({ kind: "source", source: String(r.source), ref: String(r.record_ref) })} />
      <details>
        <summary>Payroll audit log ({audit.length} calls)</summary>
        <Table rows={audit} columns={["at", "action", "employee_id", "date", "code", "http_status", "submission_id", "detail"]} />
      </details>
    </section>
  );
}
