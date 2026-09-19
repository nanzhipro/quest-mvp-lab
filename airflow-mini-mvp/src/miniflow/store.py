"""SQLite metadata store.

Mirrors Airflow's metadata database (``[database] sql_alchemy_conn``): tables
for ``dags``, ``dag_runs``, ``task_instances``, and ``xcoms``; all state
transitions are persisted here so any CLI process can inspect run history.
XCom values are JSON-serialized, as in Airflow's default JSON XCom backend.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS dags (
    dag_id      TEXT PRIMARY KEY,
    fileloc     TEXT,
    schedule    TEXT
);
CREATE TABLE IF NOT EXISTS dag_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dag_id       TEXT NOT NULL,
    run_id       TEXT NOT NULL UNIQUE,
    run_type     TEXT NOT NULL,
    logical_date TEXT NOT NULL,
    state        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_instances (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dag_id        TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    state         TEXT NOT NULL,
    try_number    INTEGER NOT NULL DEFAULT 0,
    max_tries     INTEGER NOT NULL DEFAULT 1,
    queued_at     TEXT,
    started_at    TEXT,
    ended_at      TEXT,
    next_retry_at TEXT,
    UNIQUE (dag_id, run_id, task_id)
);
CREATE TABLE IF NOT EXISTS xcoms (
    dag_id  TEXT NOT NULL,
    run_id  TEXT NOT NULL,
    task_id TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (dag_id, run_id, task_id, key)
);
"""


def utcnow() -> datetime:
    """Naive UTC now (all stored timestamps are naive UTC ISO strings)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_dt(value: str) -> datetime:
    """Parse an ISO datetime; aware inputs are normalized to naive UTC."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


class MetadataStore:
    """Thread-safe wrapper around one SQLite file (default ``./miniflow.db``)."""

    def __init__(self, path: str = "./miniflow.db"):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    # -- dags ------------------------------------------------------------

    def upsert_dag(self, dag_id: str, fileloc: str, schedule: str) -> None:
        self._execute(
            "INSERT INTO dags (dag_id, fileloc, schedule) VALUES (?, ?, ?) "
            "ON CONFLICT(dag_id) DO UPDATE SET fileloc=excluded.fileloc, schedule=excluded.schedule",
            (dag_id, fileloc, schedule),
        )

    def list_dags(self) -> list[dict]:
        return [dict(r) for r in self._execute("SELECT * FROM dags ORDER BY dag_id")]

    # -- dag runs ----------------------------------------------------------

    def create_dag_run(
        self, dag_id: str, run_id: str, logical_date: datetime, run_type: str, state: str
    ) -> dict:
        now = _iso(utcnow())
        try:
            self._execute(
                "INSERT INTO dag_runs (dag_id, run_id, run_type, logical_date, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (dag_id, run_id, run_type, _iso(logical_date), state, now, now),
            )
        except sqlite3.IntegrityError:
            pass  # run already exists — creation is idempotent
        return self.get_dag_run(dag_id, run_id)

    def get_dag_run(self, dag_id: str, run_id: str) -> dict | None:
        row = self._execute(
            "SELECT * FROM dag_runs WHERE dag_id=? AND run_id=?", (dag_id, run_id)
        ).fetchone()
        return dict(row) if row else None

    def runs_for_dag(self, dag_id: str, run_type: str | None = None) -> list[dict]:
        sql = "SELECT * FROM dag_runs WHERE dag_id=?"
        params: tuple = (dag_id,)
        if run_type:
            sql += " AND run_type=?"
            params += (run_type,)
        sql += " ORDER BY logical_date"
        return [dict(r) for r in self._execute(sql, params)]

    def active_runs(self) -> list[dict]:
        return [
            dict(r)
            for r in self._execute(
                "SELECT * FROM dag_runs WHERE state IN ('queued', 'running') ORDER BY logical_date"
            )
        ]

    def set_run_state(self, dag_id: str, run_id: str, state: str) -> None:
        self._execute(
            "UPDATE dag_runs SET state=?, updated_at=? WHERE dag_id=? AND run_id=?",
            (state, _iso(utcnow()), dag_id, run_id),
        )

    # -- task instances -----------------------------------------------------

    def ensure_ti(self, dag_id: str, run_id: str, task_id: str, max_tries: int) -> dict:
        self._execute(
            "INSERT OR IGNORE INTO task_instances (dag_id, run_id, task_id, state, max_tries) "
            "VALUES (?, ?, ?, 'none', ?)",
            (dag_id, run_id, task_id, max_tries),
        )
        return self.get_ti(dag_id, run_id, task_id)

    def get_ti(self, dag_id: str, run_id: str, task_id: str) -> dict | None:
        row = self._execute(
            "SELECT * FROM task_instances WHERE dag_id=? AND run_id=? AND task_id=?",
            (dag_id, run_id, task_id),
        ).fetchone()
        return dict(row) if row else None

    def tis_for_run(self, dag_id: str, run_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self._execute(
                "SELECT * FROM task_instances WHERE dag_id=? AND run_id=? ORDER BY id",
                (dag_id, run_id),
            )
        ]

    def set_ti_state(
        self,
        dag_id: str,
        run_id: str,
        task_id: str,
        state: str,
        *,
        try_number: int | None = None,
        queued_at: datetime | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        next_retry_at: datetime | None = None,
    ) -> None:
        sets, params = ["state=?"], [state]
        for col, val in (
            ("try_number", try_number),
            ("queued_at", _iso(queued_at)),
            ("started_at", _iso(started_at)),
            ("ended_at", _iso(ended_at)),
            ("next_retry_at", _iso(next_retry_at)),
        ):
            if val is not None:
                sets.append(f"{col}=?")
                params.append(val)
        params += [dag_id, run_id, task_id]
        self._execute(
            f"UPDATE task_instances SET {', '.join(sets)} WHERE dag_id=? AND run_id=? AND task_id=?",
            tuple(params),
        )

    def due_retries(self, now: datetime) -> list[dict]:
        return [
            dict(r)
            for r in self._execute(
                "SELECT * FROM task_instances WHERE state='up_for_retry' AND next_retry_at<=?",
                (_iso(now),),
            )
        ]

    # -- xcom ----------------------------------------------------------------

    def xcom_set(self, dag_id: str, run_id: str, task_id: str, key: str, value: Any) -> None:
        payload = json.dumps(value, default=str)
        self._execute(
            "INSERT INTO xcoms (dag_id, run_id, task_id, key, value) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(dag_id, run_id, task_id, key) DO UPDATE SET value=excluded.value",
            (dag_id, run_id, task_id, key, payload),
        )

    def xcom_get(self, dag_id: str, run_id: str, task_id: str, key: str) -> Any:
        row = self._execute(
            "SELECT value FROM xcoms WHERE dag_id=? AND run_id=? AND task_id=? AND key=?",
            (dag_id, run_id, task_id, key),
        ).fetchone()
        return json.loads(row["value"]) if row else None

    def xcoms_for_run(self, dag_id: str, run_id: str) -> list[dict]:
        rows = self._execute(
            "SELECT task_id, key, value FROM xcoms WHERE dag_id=? AND run_id=? ORDER BY task_id, key",
            (dag_id, run_id),
        )
        return [{**dict(r), "value": json.loads(r["value"])} for r in rows]
