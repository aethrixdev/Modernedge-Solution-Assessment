"""Client for the payroll system (the system of record for absences).

Auth: an `X-API-Key` header on every request.
Paging: `page` + `page_size`; the reply includes `total`.

Reads are retried freely. The one write, `create_absence`, is sent ONCE with
no automatic retry: a 503 can arrive after payroll already accepted the
absence, so a blind retry could record it twice. The payroll writer (built
later) decides when it is safe to try again, by first checking whether the
absence already exists.
"""
import requests

from .http import send


class PayrollClient:
    def __init__(self, base_url: str, api_key: str, session: requests.Session | None = None):
        """Remember the base URL and put the API key on every request."""
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers["X-API-Key"] = api_key

    # ---- helpers -----------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET one endpoint (with retries) and return the JSON; raise if it still fails."""
        resp = send(self.session, "GET", self.base_url + path, params=params)
        resp.raise_for_status()
        return resp.json()

    def _get_all_pages(self, path: str, items_key: str, params: dict | None = None,
                       page_size: int = 50) -> list[dict]:
        """Keep asking for the next page until we have `total` items."""
        items: list[dict] = []
        page = 1
        while True:
            body = self._get(path, {**(params or {}), "page": page, "page_size": page_size})
            batch = body.get(items_key, [])
            items.extend(batch)
            if not batch or len(items) >= body.get("total", 0):
                return items
            page += 1

    # ---- reads -------------------------------------------------------------
    def health(self) -> dict:
        """Check that payroll is up."""
        return self._get("/v1/health")

    def codes(self) -> list[dict]:
        """List the absence codes payroll accepts (SKP, LTN, ...)."""
        return self._get("/v1/codes")["codes"]

    def employees(self) -> list[dict]:
        """List every employee payroll knows, across all pages."""
        return self._get_all_pages("/v1/employees", "employees")

    def list_absences(self, employee_id: int | None = None, date: str | None = None,
                      status: str | None = None) -> list[dict]:
        """List submitted absences, optionally filtered by employee, date or status."""
        params = {k: v for k, v in
                  {"employee_id": employee_id, "date": date, "status": status}.items()
                  if v is not None}
        return self._get_all_pages("/v1/absences", "absences", params)

    def get_absence(self, submission_id: str) -> dict:
        """Get one submission, e.g. to read its final status."""
        return self._get(f"/v1/absences/{submission_id}")

    # ---- writes ------------------------------------------------------------
    def create_absence(self, employee_id: int, date: str, code: str, hours: float,
                       note: str | None = None) -> requests.Response:
        """Send one absence, once. Returns the raw response (202, 422, 503 ...)
        so the caller can decide what to do."""
        body = {"employee_id": employee_id, "date": date, "code": code, "hours": hours}
        if note:
            body["note"] = note
        return send(self.session, "POST", self.base_url + "/v1/absences",
                    json=body, retry=False)

    def delete_absence(self, submission_id: str) -> None:
        """Void a submission so it no longer counts in payroll."""
        resp = send(self.session, "DELETE", f"{self.base_url}/v1/absences/{submission_id}")
        resp.raise_for_status()

    def reset(self) -> None:
        """Development only: delete every submission."""
        resp = send(self.session, "POST", self.base_url + "/v1/admin/reset")
        resp.raise_for_status()
