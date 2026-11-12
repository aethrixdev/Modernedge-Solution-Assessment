"""Tests for the API clients, using fake HTTP replies (the `responses` library).

They check the tricky behaviours without needing the real systems:
retries on 503/429, paging, no retry on the payroll write, token refresh,
and re-submitting a FAILED finance report.
"""
import pytest
import responses

from pipeline.clients import http
from pipeline.clients.dispatch import DispatchClient
from pipeline.clients.finance import FinanceClient, ReportError
from pipeline.clients.payroll import PayrollClient

P = "http://payroll"
D = "http://dispatch"
F = "http://finance"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Record the waits instead of actually sleeping."""
    waits = []
    monkeypatch.setattr(http, "sleep", waits.append)
    return waits


# ---- shared retry helper ------------------------------------------------------
def test_backoff_doubles_and_is_capped():
    assert [http.backoff_delay(n) for n in (1, 2, 3, 4)] == [0.5, 1, 2, 4]
    assert http.backoff_delay(20) == 30.0


@responses.activate
def test_get_retries_503_then_succeeds(no_waiting):
    responses.get(f"{P}/v1/codes", status=503)
    responses.get(f"{P}/v1/codes", status=503)
    responses.get(f"{P}/v1/codes", json={"codes": [{"code": "SKP"}]})
    assert PayrollClient(P, "k").codes() == [{"code": "SKP"}]
    assert no_waiting == [0.5, 1]


@responses.activate
def test_429_waits_for_retry_after(no_waiting):
    responses.post(f"{D}/oauth/token", json={"access_token": "t", "expires_in": 300})
    responses.get(f"{D}/v2/sites", status=429, headers={"Retry-After": "7"})
    responses.get(f"{D}/v2/sites", json={"data": [{"site_id": "S1"}], "next_cursor": None})
    assert DispatchClient(D, "id", "secret").sites() == [{"site_id": "S1"}]
    assert no_waiting == [7.0]


# ---- payroll --------------------------------------------------------------------
@responses.activate
def test_payroll_sends_api_key_and_reads_all_pages():
    responses.get(f"{P}/v1/employees", json={"total": 3, "employees": [{"employee_id": 1}, {"employee_id": 2}]})
    responses.get(f"{P}/v1/employees", json={"total": 3, "employees": [{"employee_id": 3}]})
    ids = [e["employee_id"] for e in PayrollClient(P, "secret-key").employees()]
    assert ids == [1, 2, 3]
    assert responses.calls[0].request.headers["X-API-Key"] == "secret-key"
    assert "page=2" in responses.calls[1].request.url


@responses.activate
def test_payroll_write_is_never_retried_automatically():
    responses.post(f"{P}/v1/absences", status=503)
    resp = PayrollClient(P, "k").create_absence(10001, "2026-10-14", "SKP", 8)
    assert resp.status_code == 503
    assert len(responses.calls) == 1  # sent exactly once


# ---- dispatch -------------------------------------------------------------------
@responses.activate
def test_dispatch_follows_cursor_until_empty():
    responses.post(f"{D}/oauth/token", json={"access_token": "t", "expires_in": 300})
    responses.get(f"{D}/v2/workers", json={"data": [{"worker_id": "W1"}], "next_cursor": "abc"})
    responses.get(f"{D}/v2/workers", json={"data": [{"worker_id": "W2"}], "next_cursor": None})
    workers = DispatchClient(D, "id", "secret").workers()
    assert [w["worker_id"] for w in workers] == ["W1", "W2"]
    assert "cursor=abc" in responses.calls[2].request.url


@responses.activate
def test_dispatch_refreshes_token_when_it_expires():
    now = [0.0]
    responses.post(f"{D}/oauth/token", json={"access_token": "first", "expires_in": 300})
    responses.post(f"{D}/oauth/token", json={"access_token": "second", "expires_in": 300})
    responses.get(f"{D}/v2/sites", json={"data": []})
    client = DispatchClient(D, "id", "secret", clock=lambda: now[0])
    client.sites()
    now[0] = 280  # past 300 - 30s safety margin
    client.sites()
    auth = [c.request.headers.get("Authorization") for c in responses.calls if "/v2/" in c.request.url]
    assert auth == ["Bearer first", "Bearer second"]


@responses.activate
def test_dispatch_retries_once_on_401():
    responses.post(f"{D}/oauth/token", json={"access_token": "old", "expires_in": 300})
    responses.post(f"{D}/oauth/token", json={"access_token": "new", "expires_in": 300})
    responses.get(f"{D}/v2/sites", status=401, json={"error": "invalid_token"})
    responses.get(f"{D}/v2/sites", json={"data": [{"site_id": "S1"}]})
    assert DispatchClient(D, "id", "secret").sites() == [{"site_id": "S1"}]


# ---- finance --------------------------------------------------------------------
@responses.activate
def test_finance_resubmits_failed_report():
    responses.post(f"{F}/api/v1/reports", json={"job_id": "J1"})
    responses.post(f"{F}/api/v1/reports", json={"job_id": "J2"})
    responses.get(f"{F}/api/v1/reports/J1", json={"status": "FAILED", "error": "boom"})
    responses.get(f"{F}/api/v1/reports/J2", json={"status": "RUNNING"})
    responses.get(f"{F}/api/v1/reports/J2", json={"status": "COMPLETE"})
    responses.get(f"{F}/api/v1/reports/J2/file", body="REPORT TEXT")
    client = FinanceClient(F, "u", "p", poll_interval=0)
    assert client.run_report("INV_REG", "NBS", "2026-10") == "REPORT TEXT"


@responses.activate
def test_finance_gives_up_after_max_submits():
    responses.post(f"{F}/api/v1/reports", json={"job_id": "J1"})
    responses.get(f"{F}/api/v1/reports/J1", json={"status": "FAILED", "error": "boom"})
    client = FinanceClient(F, "u", "p", poll_interval=0, max_submits=2)
    with pytest.raises(ReportError):
        client.run_report("INV_REG", "NBS", "2026-10")


@responses.activate
def test_finance_customers_pages_by_offset():
    responses.get(f"{F}/api/v1/customers", json={"items": [{"customer_no": "C1"}], "total": 2})
    responses.get(f"{F}/api/v1/customers", json={"items": [{"customer_no": "C2"}], "total": 2})
    customers = FinanceClient(F, "u", "p").customers()
    assert [c["customer_no"] for c in customers] == ["C1", "C2"]
    assert "offset=1" in responses.calls[1].request.url
