"""API and reviewer decisions, offline: a throwaway SQLite file stands in for Postgres."""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from api import main
from api.review import apply_decision
from pipeline import db

ITEM = {"absence_id": "E1-2026-10-19", "hr_id": "E1", "payroll_id": None, "date": "2026-10-19",
        "source": "reception_log", "source_ref": "14", "code": None}


def sqlite(tmp_path):
    """A SQLite database with one held item in review_queue."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE review_queue (absence_id text)"))
        conn.execute(text("INSERT INTO review_queue VALUES ('E1-2026-10-19')"))
    return url, engine


def test_reject_is_recorded_and_the_next_run_sees_it(tmp_path, monkeypatch):
    url, engine = sqlite(tmp_path)
    assert apply_decision(engine, ITEM, "reject", None, "not an absence") == {"absence_id": "E1-2026-10-19", "action": "reject"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM review_queue")).scalar() == 0      # off the queue
    monkeypatch.setenv("DATABASE_URL", url)
    assert db.reviewer_decisions() == {"E1-2026-10-19": {"action": "reject", "code": None, "note": "not an absence"}}


def test_approve_without_a_known_employee_writes_nothing(tmp_path):
    _, engine = sqlite(tmp_path)
    assert "error" in apply_decision(engine, ITEM, "approve", "SKP", "")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM review_queue")).scalar() == 1      # still held


def test_no_database_means_no_reviewer_decisions(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.reviewer_decisions() == {}


def test_decide_endpoint_checks_action_and_item(monkeypatch):
    monkeypatch.setattr(main, "rows", lambda sql, **p: [])
    api = TestClient(main.app)
    assert api.post("/api/review-queue/x", json={"action": "maybe"}).status_code == 422
    assert api.post("/api/review-queue/x", json={"action": "reject"}).status_code == 404


def test_source_shows_the_time_clock_rows_behind_a_figure(monkeypatch, tmp_path):
    (tmp_path / "clock.csv").write_text("badge,punch\nB1,07:02\nB2,15:01\n", encoding="utf-8")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    got = TestClient(main.app).get("/api/source", params={"source": "time_clock", "ref": "clock.csv#3"}).json()
    assert got == [{"file": "clock.csv", "row": 3, "badge": "B2", "punch": "15:01"}]
