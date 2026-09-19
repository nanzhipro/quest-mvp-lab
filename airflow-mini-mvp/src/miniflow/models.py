"""State machines, runtime handles, and exceptions.

Mirrors Airflow concepts:
- ``TaskInstanceState``  -> ``airflow.utils.state.TaskInstanceState``
- ``DagRunState``        -> ``airflow.utils.state.DagRunState``
- ``DagCycleError``      -> ``airflow.exceptions.AirflowDagCycleException``
- ``XCOM_RETURN_KEY``    -> ``airflow.models.xcom.XCOM_RETURN_KEY`` (``"return_value"``)
- ``TaskInstanceHandle`` -> the ``ti`` object task code receives via context
  (``airflow.models.taskinstance.TaskInstance``), exposing ``xcom_pull``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

XCOM_RETURN_KEY = "return_value"


class TaskInstanceState:
    """Airflow TaskInstance states (toy subset, same names/values)."""

    NONE = "none"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    UP_FOR_RETRY = "up_for_retry"
    SKIPPED = "skipped"
    UPSTREAM_FAILED = "upstream_failed"

    TERMINAL = frozenset({SUCCESS, FAILED, SKIPPED, UPSTREAM_FAILED})


class DagRunState:
    """Airflow DagRun states."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

    TERMINAL = frozenset({SUCCESS, FAILED})


class DagCycleError(Exception):
    """Raised when dependency wiring would introduce a cycle.

    Airflow counterpart: ``AirflowDagCycleException``.
    """


class DuplicateTaskIdError(Exception):
    """Raised when two tasks share a task_id in one DAG.

    Airflow counterpart: ``DuplicateTaskIdFound``.
    """


@dataclass
class DagRun:
    """Row view of a DagRun. Mirrors ``airflow.models.DagRun``."""

    dag_id: str
    run_id: str
    logical_date: datetime
    state: str
    run_type: str  # "scheduled" | "manual" | "backfill"


class TaskInstanceHandle:
    """Runtime view of one TaskInstance row, passed to task callables as ``ti``.

    Task code uses it exactly like Airflow's ``context["ti"]``:
    ``ti.xcom_pull(task_ids="extract")``, ``ti.try_number``, ``ti.state``.
    """

    def __init__(self, store, dag_id: str, run_id: str, task_id: str):
        self._store = store
        self.dag_id = dag_id
        self.run_id = run_id
        self.task_id = task_id

    def _row(self) -> dict:
        return self._store.get_ti(self.dag_id, self.run_id, self.task_id)

    @property
    def state(self) -> str:
        return self._row()["state"]

    @property
    def try_number(self) -> int:
        return self._row()["try_number"]

    def xcom_pull(
        self, task_ids: str | list[str] | None = None, key: str = XCOM_RETURN_KEY
    ) -> Any:
        """Pull an XCom value from sibling tasks in the same DagRun.

        Mirrors ``TaskInstance.xcom_pull``: a single task_id returns one value,
        a list returns a list in the same order.
        """
        single = isinstance(task_ids, str)
        ids = [task_ids] if single else list(task_ids or [])
        values = [self._store.xcom_get(self.dag_id, self.run_id, t, key) for t in ids]
        return values[0] if single else values
