"""Tests for the API clients, using fake HTTP replies (the `responses` library).

They check the tricky behaviours without needing the real systems:
retries on 503, paging, and no retry on the payroll write.
"""
import pytest
import responses

from pipeline.clients import http
from pipeline.clients.payroll import PayrollClient

P = "http://payroll"


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
