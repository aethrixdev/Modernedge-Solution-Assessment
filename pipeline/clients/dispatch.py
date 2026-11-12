"""Client for the dispatch app (shift postings and who covered them).

Auth: OAuth2 "client credentials". We swap the client id + secret for an
access token, then send `Authorization: Bearer <token>`. The token only lives
300 seconds, so:
- we remember when it expires and fetch a new one 30s early, and
- if the server still says 401 (token expired), we fetch a new one and retry once.

Paging: replies have `data` + `next_cursor`; send `cursor=<next_cursor>` to get
the next page, until `next_cursor` is empty. `limit` is at most 50.
Rate limit: 429 + Retry-After, handled by the shared `send()` helper.
"""
import time

import requests

from .http import send

TOKEN_SAFETY_MARGIN = 30  # seconds: refresh a bit before the real expiry
MAX_PAGE_SIZE = 50


class DispatchClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str,
                 session: requests.Session | None = None, clock=time.monotonic):
        """Remember the credentials; the token is fetched on first use."""
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self.clock = clock  # tests can pass a fake clock
        self._token: str | None = None
        self._token_expires_at = 0.0

    # ---- auth --------------------------------------------------------------
    def _fetch_token(self) -> None:
        """Swap the client id + secret for a new access token and note when it expires."""
        resp = send(self.session, "POST", self.base_url + "/oauth/token", data={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = self.clock() + body.get("expires_in", 300) - TOKEN_SAFETY_MARGIN

    def _auth_header(self) -> dict:
        """Return the Bearer header, fetching a new token first if ours is missing or about to expire."""
        if self._token is None or self.clock() >= self._token_expires_at:
            self._fetch_token()
        return {"Authorization": f"Bearer {self._token}"}

    # ---- helpers -----------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET one endpoint with the token; on a 401, get a new token and try once more."""
        url = self.base_url + path
        resp = send(self.session, "GET", url, params=params, headers=self._auth_header())
        if resp.status_code == 401:  # token expired early: get a new one, try once more
            self._token = None
            resp = send(self.session, "GET", url, params=params, headers=self._auth_header())
        resp.raise_for_status()
        return resp.json()

    def _get_all_pages(self, path: str, params: dict | None = None) -> list[dict]:
        """Follow next_cursor page by page until it is empty."""
        items: list[dict] = []
        cursor = None
        while True:
            query = {**(params or {}), "limit": MAX_PAGE_SIZE}
            if cursor:
                query["cursor"] = cursor
            body = self._get(path, query)
            items.extend(body.get("data", []))
            cursor = body.get("next_cursor")
            if not cursor:
                return items

    # ---- reads -------------------------------------------------------------
    def sites(self) -> list[dict]:
        """List all dispatch sites (buildings)."""
        return self._get_all_pages("/v2/sites")

    def workers(self) -> list[dict]:
        """List all workers dispatch knows."""
        return self._get_all_pages("/v2/workers")

    def worker(self, worker_id: str) -> dict:
        """Get one worker."""
        return self._get(f"/v2/workers/{worker_id}")

    def postings(self, site_id: str | None = None, status: str | None = None,
                 updated_since: str | None = None) -> list[dict]:
        """List shift postings (open shifts and who filled them), optionally filtered."""
        params = {k: v for k, v in
                  {"site_id": site_id, "status": status, "updated_since": updated_since}.items()
                  if v is not None}
        return self._get_all_pages("/v2/postings", params)

    def posting(self, posting_id: str) -> dict:
        """Get one posting."""
        return self._get(f"/v2/postings/{posting_id}")
