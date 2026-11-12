"""Client for the finance ERP.

Auth: HTTP Basic (username + password) on every request.
Paging (customers only): `offset` + `limit` (at most 100); reply has `total`.

Reports are asynchronous:
  1. POST /api/v1/reports            -> job_id
  2. GET  /api/v1/reports/{job_id}   -> QUEUED / RUNNING / COMPLETE / FAILED
  3. GET  /api/v1/reports/{job_id}/file  (only once COMPLETE)
A FAILED job must be submitted again, so `run_report` re-submits a few times
before giving up.
"""
import time

import requests

from .http import send

MAX_PAGE_SIZE = 100


class ReportError(RuntimeError):
    """A report job failed too many times or never finished."""


class FinanceClient:
    def __init__(self, base_url: str, user: str, password: str,
                 session: requests.Session | None = None,
                 poll_interval: float = 1.0, poll_timeout: float = 120.0, max_submits: int = 5):
        """Remember the base URL, use HTTP Basic auth, and set how long to wait for reports."""
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.auth = (user, password)
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.max_submits = max_submits

    # ---- helpers -----------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET one endpoint (with retries) and return the JSON; raise if it still fails."""
        resp = send(self.session, "GET", self.base_url + path, params=params)
        resp.raise_for_status()
        return resp.json()

    # ---- reference data ----------------------------------------------------
    def entities(self) -> list[dict]:
        """List the legal entities (each has its own books and reports)."""
        return self._get("/api/v1/entities")["items"]

    def sites(self) -> list[dict]:
        """List finance's sites, with the ledger department each one is charged to."""
        return self._get("/api/v1/sites")["items"]

    def customers(self, entity_code: str | None = None) -> list[dict]:
        """List customers, page by page using offset + limit."""
        items: list[dict] = []
        offset = 0
        while True:
            params = {"offset": offset, "limit": MAX_PAGE_SIZE}
            if entity_code:
                params["entity_code"] = entity_code
            body = self._get("/api/v1/customers", params)
            batch = body.get("items", [])
            items.extend(batch)
            offset += len(batch)
            if not batch or offset >= body.get("total", 0):
                return items

    # ---- reports -----------------------------------------------------------
    def _submit(self, report_code: str, entity_code: str, period: str) -> str:
        """Start one report job and return its job id."""
        resp = send(self.session, "POST", self.base_url + "/api/v1/reports", json={
            "report_code": report_code, "entity_code": entity_code, "period": period,
        })
        resp.raise_for_status()
        return resp.json()["job_id"]

    def _wait(self, job_id: str) -> dict:
        """Poll until the job is COMPLETE or FAILED (or we time out)."""
        deadline = time.monotonic() + self.poll_timeout
        while True:
            job = self._get(f"/api/v1/reports/{job_id}")
            if job["status"] in ("COMPLETE", "FAILED"):
                return job
            if time.monotonic() > deadline:
                raise ReportError(f"report job {job_id} still {job['status']} after {self.poll_timeout}s")
            time.sleep(self.poll_interval)

    def run_report(self, report_code: str, entity_code: str, period: str) -> str:
        """Run one report end to end and return the file text exactly as printed."""
        errors = []
        for _ in range(self.max_submits):
            job_id = self._submit(report_code, entity_code, period)
            job = self._wait(job_id)
            if job["status"] == "COMPLETE":
                resp = send(self.session, "GET", f"{self.base_url}/api/v1/reports/{job_id}/file")
                resp.raise_for_status()
                return resp.text
            errors.append(f"{job_id}: {job.get('error')}")
        raise ReportError(f"{report_code}/{entity_code}/{period} failed {self.max_submits} times: {errors}")
