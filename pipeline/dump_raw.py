"""Download everything the three systems expose and save it to ./raw/.

Run with:  python -m pipeline.dump_raw

This is our first end-to-end check: if this works, auth, paging, retries and
the async report flow all work. The saved files are also what we profile next.
Payroll is only READ here (employees, codes, existing absences), never written.
"""
import json
import logging
import os
import sys
from pathlib import Path

from .clients.dispatch import DispatchClient
from .clients.finance import FinanceClient
from .clients.payroll import PayrollClient
from .config import load_settings

REPORT_CODES = ["INV_REG", "AR_OPEN", "GL_LABOR"]  # defined by the finance API


def save_json(folder: Path, name: str, data) -> None:
    """Write data as pretty JSON and print how many records it holds."""
    path = folder / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    count = len(data) if isinstance(data, list) else 1
    print(f"  {path}  ({count} records)")


def main(period: str = os.getenv("PERIOD", "2026-10")) -> None:
    """Download everything from payroll, dispatch and finance into ./raw/ (payroll is only read)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = load_settings()
    raw = Path("raw")

    print("Payroll")
    out = raw / "payroll"
    out.mkdir(parents=True, exist_ok=True)
    payroll = PayrollClient(s.payroll_url, s.payroll_api_key)
    save_json(out, "codes", payroll.codes())
    save_json(out, "employees", payroll.employees())
    save_json(out, "absences", payroll.list_absences())

    print("Dispatch")
    out = raw / "dispatch"
    out.mkdir(parents=True, exist_ok=True)
    dispatch = DispatchClient(s.dispatch_url, s.dispatch_client_id, s.dispatch_client_secret)
    save_json(out, "sites", dispatch.sites())
    save_json(out, "workers", dispatch.workers())
    save_json(out, "postings", dispatch.postings())

    print("Finance")
    out = raw / "finance"
    out.mkdir(parents=True, exist_ok=True)
    finance = FinanceClient(s.finance_url, s.finance_user, s.finance_password)
    entities = finance.entities()
    save_json(out, "entities", entities)
    save_json(out, "customers", finance.customers())
    save_json(out, "sites", finance.sites())
    # One file per report per legal entity (entities discovered, not hard-coded).
    for entity in entities:
        code = entity["entity_code"]
        for report in REPORT_CODES:
            text = finance.run_report(report, code, period)
            path = out / f"{report}_{code}_{period}.txt"
            path.write_text(text, encoding="utf-8")
            print(f"  {path}  ({len(text.splitlines())} lines)")


if __name__ == "__main__":
    main(*sys.argv[1:])
