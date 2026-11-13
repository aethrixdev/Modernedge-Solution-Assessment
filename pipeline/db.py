"""Load everything into Postgres so the API, UI and chatbot can query it.

Layers (all in schema `public`):
- raw_*      : source records as received (one row per source row/record)
- dim_*      : resolved master data - one row per employee, building, ...
- fact_*     : derived records (absences, coverage shifts, KPI facts ...)
- out_*      : the scored outputs, as tables
Every derived row keeps a source + source_ref so it can be traced back.

Tables are rebuilt on each run (the pipeline is a full refresh).
"""
import logging
import os
from dataclasses import asdict, is_dataclass
from decimal import Decimal

import pandas as pd
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)


def _frame(rows) -> pd.DataFrame:
    """Rows -> a pandas table that Postgres accepts: Decimals become floats, lists become text."""
    rows = [asdict(r) if is_dataclass(r) else dict(r) for r in rows]
    df = pd.DataFrame(rows)
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = df[col].map(lambda v: float(v) if isinstance(v, Decimal) else v)
        if df[col].map(lambda v: isinstance(v, (list, dict, set))).any():
            df[col] = df[col].map(lambda v: "; ".join(map(str, v)) if isinstance(v, (list, set)) else str(v))
    return df


def load(tables: dict[str, list]) -> bool:
    """Replace every table with this run's rows (a full refresh). Skipped if DATABASE_URL isn't set."""
    url = os.getenv("DATABASE_URL")
    if not url:
        log.info("DATABASE_URL not set: skipping the Postgres load")
        return False
    engine = create_engine(url)
    with engine.begin() as conn:
        for name, rows in tables.items():
            conn.execute(text(f'DROP TABLE IF EXISTS "{name}" CASCADE'))
            df = _frame(rows)
            if df.empty:
                continue
            df.to_sql(name, conn, index=False)
    log.info("loaded %d tables into Postgres", len(tables))
    return True
