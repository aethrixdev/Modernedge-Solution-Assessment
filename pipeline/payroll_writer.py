"""Write approved absences to payroll safely (Part B step 6).

Guarantees:
- DRY RUN: with DRY_RUN=true nothing is sent; the audit log shows what would be.
- NO DUPLICATES: before every POST we ask payroll what it already holds for
  that employee and date. Our idempotency key is (payroll employee id, date):
  one absence per employee per date.
    * same code already there      -> adopt it, do not post again
    * our own entry, wrong code    -> void it, then post the right one
    * someone else's entry there   -> do not touch it; hold for review
- SAFE RETRIES: a 503 can arrive AFTER payroll accepted the absence (a lost
  202). So after any failed attempt we look again before re-posting; only if
  nothing landed do we post again, with backoff.
- AUDIT: every call (look-up, post, void, poll) is recorded.
- FINAL STATUS: payroll processes in batches, so we poll each submission
  until it leaves RECEIVED (POSTED, or REJECTED) and report that status.
"""
import logging
import time
from datetime import datetime, timezone

import requests

from .callouts import Absence
from .clients import http
from .clients.payroll import PayrollClient

log = logging.getLogger(__name__)

MARKER = "[callout-pipeline]"         # tags the entries this system wrote
LIVE = lambda s: s.get("status") not in ("VOIDED", "REJECTED")  # noqa: E731


class PayrollWriter:
    """Writes each approved absence to payroll exactly once, and records every call in an audit log."""
    def __init__(self, client: PayrollClient, dry_run: bool, max_attempts: int = 6,
                 poll_timeout: float = 240, poll_interval: float = 3):
        """Set dry-run, how many tries a write gets, and how long to wait for payroll to process."""
        self.client = client
        self.dry_run = dry_run
        self.max_attempts = max_attempts
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval
        self.audit: list[dict] = []

    def _log(self, a: Absence, action: str, status=None, submission_id="", detail=""):
        """Add one line to the audit log."""
        self.audit.append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "action": action,
            "employee_id": a.payroll_id, "date": a.day.isoformat(), "code": a.code,
            "http_status": status, "submission_id": submission_id, "detail": str(detail)[:500],
            "source_ref": a.primary.ref, "dry_run": self.dry_run,
        })

    def _existing(self, a: Absence) -> list[dict]:
        """Ask payroll what it already holds (not voided/rejected) for this employee and date."""
        found = [s for s in self.client.list_absences(employee_id=a.payroll_id, date=a.day.isoformat()) if LIVE(s)]
        self._log(a, "lookup", 200, ",".join(s["submission_id"] for s in found), f"{len(found)} live entr(y/ies)")
        return found

    def note(self, a: Absence) -> str:
        """The note sent with each entry: our marker plus where the call-out came from."""
        r = a.primary
        when = f"{a.received_at:%m/%d %H:%M}" if a.received_at else "unknown time"
        return f"{MARKER} {r.report.source} {r.report.source_ref}; call-out received {when}"

    # ---- one absence -----------------------------------------------------------------
    def write(self, a: Absence) -> None:
        """Hold, dry-run, adopt an existing entry, or post once with safe retries."""
        if a.review:
            a.decision = "held_for_review"
            return
        a.decision = "written"
        if self.dry_run:
            a.final_status = "DRY_RUN"
            self._log(a, "dry_run", detail=f"would POST {a.code} for {a.payroll_id} on {a.day}")
            return

        existing = self._existing(a)
        same = [s for s in existing if s.get("code") == a.code]
        ours_wrong = [s for s in existing if s.get("code") != a.code and MARKER in (s.get("note") or "")]
        foreign = [s for s in existing if s.get("code") != a.code and MARKER not in (s.get("note") or "")]
        if foreign:
            a.decision, a.final_status = "held_for_review", ""
            a.review.append("payroll already holds a different entry for this date that this system did not write: "
                            + ", ".join(f"{s['submission_id']} {s['code']}" for s in foreign))
            self._log(a, "held", detail=a.review[-1])
            return
        for s in ours_wrong:
            self.client.delete_absence(s["submission_id"])
            self._log(a, "void", 200, s["submission_id"], f"replaced earlier code {s['code']}")
        if same:
            keep, *extra = same
            for s in extra:   # duplicates from an earlier faulty run: keep one
                if MARKER in (s.get("note") or ""):
                    self.client.delete_absence(s["submission_id"])
                    self._log(a, "void", 200, s["submission_id"], "duplicate of " + keep["submission_id"])
            a.submission_id = keep["submission_id"]
            self._log(a, "adopt", 200, keep["submission_id"], "already in payroll with the same code")
            return

        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.client.create_absence(a.payroll_id, a.day.isoformat(), a.code, 8, self.note(a))
                status = resp.status_code
            except (requests.ConnectionError, requests.Timeout) as exc:
                resp, status = None, None
                self._log(a, "post", None, detail=f"attempt {attempt}: {exc}")
            if resp is not None and status == 202:
                a.submission_id = resp.json()["submission_id"]
                self._log(a, "post", 202, a.submission_id, f"attempt {attempt}")
                return
            if resp is not None and status == 422:
                a.decision = "held_for_review"
                body = resp.json()
                err = body.get("detail", body)
                a.review.append(f"payroll rejected the entry: {err}")
                self._log(a, "post", 422, detail=err)
                return
            if resp is not None and status not in http.RETRY_STATUSES:
                a.decision = "held_for_review"
                a.review.append(f"payroll returned HTTP {status}")
                self._log(a, "post", status, detail=resp.text)
                return
            if resp is not None:
                self._log(a, "post", status, detail=f"attempt {attempt}: will check before retrying")
            # The request may have landed even though we got an error: look first.
            landed = [s for s in self._existing(a) if s.get("code") == a.code]
            if landed:
                a.submission_id = landed[0]["submission_id"]
                self._log(a, "adopt", 200, a.submission_id, "found after a failed attempt (lost 202)")
                return
            delay = (http.retry_after_seconds(resp) if resp is not None else None) or http.backoff_delay(attempt)
            http.sleep(delay)
        a.decision = "held_for_review"
        a.review.append(f"payroll unavailable after {self.max_attempts} attempts")

    # ---- after all writes: wait for processing ---------------------------------------
    def collect_final_status(self, absences: list[Absence]) -> None:
        """Poll every submission until payroll has processed it (POSTED or REJECTED)."""
        pending = {a.submission_id: a for a in absences if a.submission_id}
        deadline = time.monotonic() + self.poll_timeout
        while pending and time.monotonic() < deadline:
            for sid, a in list(pending.items()):
                try:
                    status = self.client.get_absence(sid).get("status", "")
                except requests.HTTPError as exc:
                    self._log(a, "poll", None, sid, exc)
                    continue
                if status and status != "RECEIVED":
                    a.final_status = status
                    self._log(a, "poll", 200, sid, status)
                    del pending[sid]
            if pending:
                time.sleep(self.poll_interval)
        for sid, a in pending.items():
            a.final_status = "RECEIVED"
            self._log(a, "poll", None, sid, "still RECEIVED when polling stopped")
