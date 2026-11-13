"""Loading into Postgres."""
import os
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from pipeline import db


@dataclass
class Row:
    name: str
    amount: Decimal
    tags: list


def test_rows_become_a_table_postgres_accepts():
    df = db._frame([Row("P-1", Decimal("299.82"), ["reassigned", "OT"]), {"name": "P-2", "amount": None, "tags": []}])
    assert list(df.columns) == ["name", "amount", "tags"]
    assert df["amount"][0] == 299.82 and isinstance(df["amount"][0], float)   # Decimal -> float
    assert df["tags"][0] == "reassigned; OT"                                  # list -> text


def test_without_database_url_the_load_is_skipped(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.load({"t": [{"a": 1}]}) is False


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs a running Postgres (DATABASE_URL)")
def test_load_replaces_tables_on_each_run():
    db.load({"test_table": [{"a": 1}, {"a": 2}]})
    db.load({"test_table": [{"a": 3}]})                                       # full refresh, not append
    with create_engine(os.environ["DATABASE_URL"]).begin() as conn:
        assert conn.execute(text("SELECT a FROM test_table")).scalars().all() == [3]
        conn.execute(text("DROP TABLE test_table"))
