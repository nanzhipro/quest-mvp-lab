"""Scheduler loop.

Mirrors ``airflow.jobs.scheduler_job``: each tick
  1. syncs DAG definitions into the metadata DB,
  2. creates due DagRuns per schedule (with Airflow ``catchup`` semantics —
     catchup=False schedules only the latest interval),
  3. promotes TaskInstances whose upstream dependencies all succeeded to
     ``queued`` and hands them to the executor,
  4. re-queues ``up_for_retry`` TaskInstances once their retry delay elapses,
  5. finalizes DagRun state once every TI is terminal.

Task execution job transitions mirror Airflow's TaskInstance lifecycle:
queued -> running -> success | failed | up_for_retry (with auto XCom push of
the return value under ``return_value``).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from .dag import DAG
from .executors import SequentialExecutor
from .loading import DagBag, load_dags
from .models import (
    XCOM_RETURN_KEY,
    DagRunState,
    TaskInstanceHandle,
    TaskInstanceState as TI,
)
from .schedule import OnceSchedule, describe_schedule
from .store import MetadataStore, utcnow

SCHEDULED_RUN_PREFIX = "scheduled__"
MANUAL_RUN_PREFIX = "manual__"
BACKFILL_RUN_PREFIX = "backfill__"


class TaskInstanceJob:
    """Executable unit handed to the executor (Airflow's LocalTaskJob, toy-scale)."""

    def __init__(self, store: MetadataStore, dag: DAG, run_id: str, task_id: str, now_fn=utcnow):
        self.store = store
        self.dag = dag
        self.run_id = run_id
        self.task_id = task_id
        self.now_fn = now_fn

    def run(self) -> None:
        store, dag_id, run_id, task_id = self.store, self.dag.dag_id, self.run_id, self.task_id
        task = self.dag.tasks[task_id]
        row = store.get_ti(dag_id, run_id, task_id)
        try_number = row["try_number"] + 1
        store.set_ti_state(
            dag_id, run_id, task_id, TI.RUNNING, try_number=try_number, started_at=self.now_fn()
        )
        handle = TaskInstanceHandle(store, dag_id, run_id, task_id)
        run_row = store.get_dag_run(dag_id, run_id)
        context = {
            "ti": handle,
            "dag": self.dag,
            "run_id": run_id,
            "logical_date": _parse_stored(run_row["logical_date"]),
        }
        try:
            result = task.execute(context)
        except Exception:
            if try_number <= task.retries:
                # Mirrors Airflow: not the last try -> up_for_retry + delayed requeue.
                store.set_ti_state(
                    dag_id, run_id, task_id, TI.UP_FOR_RETRY,
                    next_retry_at=self.now_fn() + task.retry_delay,
                )
            else:
                store.set_ti_state(dag_id, run_id, task_id, TI.FAILED, ended_at=self.now_fn())
            return
        # Mirrors Airflow: any non-None return value is auto-pushed to XCom
        # under XCOM_RETURN_KEY.
        if result is not None:
            store.xcom_set(dag_id, run_id, task_id, XCOM_RETURN_KEY, result)
        store.set_ti_state(dag_id, run_id, task_id, TI.SUCCESS, ended_at=self.now_fn())


class Scheduler:
    """Toy scheduler. ``tick()`` is one pass; ``run()`` loops until idle."""

    def __init__(
        self,
        store: MetadataStore,
        dags_folder: str = "./dags",
        executor=None,
        now_fn=utcnow,
    ):
        self.store = store
        self.dags_folder = dags_folder
        self.executor = executor or SequentialExecutor()
        self.now_fn = now_fn
        self._bag: DagBag | None = None

    def bag(self) -> DagBag:
        if self._bag is None:
            self._bag = load_dags(self.dags_folder)
        return self._bag

    # -- one scheduling pass --------------------------------------------------

    def tick(self) -> None:
        now = self.now_fn()
        bag = self.bag()
        for dag in bag.dags.values():
            self.store.upsert_dag(dag.dag_id, dag.fileloc, describe_schedule(dag.schedule))
        for dag in bag.dags.values():
            self._create_due_runs(dag, now)
        for run in self.store.active_runs():
            self._admit(bag, run)
        self._requeue_retries(bag, now)
        self.executor.sync()
        for run in self.store.active_runs():
            self._finalize(run)

    # -- DagRun creation --------------------------------------------------------

    def _create_due_runs(self, dag: DAG, now: datetime) -> None:
        schedule = dag.schedule
        if schedule is None:
            return
        existing = self.store.runs_for_dag(dag.dag_id, run_type="scheduled")
        if isinstance(schedule, OnceSchedule):
            if not existing:
                start = dag.start_date or now
                self._create_run(dag, start, SCHEDULED_RUN_PREFIX)
            return
        anchor = dag.start_date or now
        cand = _parse_stored(existing[-1]["logical_date"]) if existing else anchor
        if existing:
            cand = schedule.next_after(cand)
        latest = None
        while True:
            # A run with logical_date L is ready once its interval ends (next fire <= now).
            end = schedule.next_after(cand)
            if end is None or end > now:
                break
            latest = cand
            cand = end
        if latest is None:
            return
        if dag.catchup:
            # Airflow catchup=True: schedule every ready interval since start_date.
            first = _parse_stored(existing[-1]["logical_date"]) if existing else anchor
            if existing:
                first = schedule.next_after(first)
            t = first
            while t <= latest:
                self._create_run(dag, t, SCHEDULED_RUN_PREFIX)
                t = schedule.next_after(t)
        else:
            # Airflow catchup=False: only the latest ready interval is scheduled.
            self._create_run(dag, latest, SCHEDULED_RUN_PREFIX)

    def _create_run(self, dag: DAG, logical_date: datetime, prefix: str) -> dict:
        run_id = f"{prefix}{logical_date.isoformat()}"
        return self.store.create_dag_run(
            dag.dag_id, run_id, logical_date, run_type=prefix.rstrip("_"), state=DagRunState.QUEUED
        )

    # -- TaskInstance admission ---------------------------------------------------

    def _admit(self, bag: DagBag, run: dict) -> None:
        dag = bag.get_dag(run["dag_id"])
        if dag is None:
            return
        dag_id, run_id = run["dag_id"], run["run_id"]
        for task in dag.tasks.values():
            self.store.ensure_ti(dag_id, run_id, task.task_id, max_tries=task.retries + 1)
        tis = {t["task_id"]: t for t in self.store.tis_for_run(dag_id, run_id)}
        for task in dag.topo_sort():
            ti = tis[task.task_id]
            if ti["state"] != TI.NONE:
                continue
            ups = [tis[u]["state"] for u in task.upstream_task_ids]
            if any(s == TI.SKIPPED for s in ups):
                self.store.set_ti_state(dag_id, run_id, task.task_id, TI.SKIPPED, ended_at=self.now_fn())
            elif any(s in (TI.FAILED, TI.UPSTREAM_FAILED) for s in ups):
                self.store.set_ti_state(
                    dag_id, run_id, task.task_id, TI.UPSTREAM_FAILED, ended_at=self.now_fn()
                )
            elif all(s == TI.SUCCESS for s in ups):
                self.store.set_ti_state(dag_id, run_id, task.task_id, TI.SCHEDULED)
                self._queue(dag, run_id, task.task_id)
        if run["state"] == DagRunState.QUEUED and any(
            t["state"] in (TI.QUEUED, TI.RUNNING) for t in tis.values()
        ):
            self.store.set_run_state(dag_id, run_id, DagRunState.RUNNING)

    def _queue(self, dag: DAG, run_id: str, task_id: str) -> None:
        self.store.set_ti_state(dag.dag_id, run_id, task_id, TI.QUEUED, queued_at=self.now_fn())
        self.executor.execute(TaskInstanceJob(self.store, dag, run_id, task_id, now_fn=self.now_fn))

    def _requeue_retries(self, bag: DagBag, now: datetime) -> None:
        for ti in self.store.due_retries(now):
            dag = bag.get_dag(ti["dag_id"])
            if dag is not None:
                self._queue(dag, ti["run_id"], ti["task_id"])

    # -- DagRun finalization -------------------------------------------------------

    def _finalize(self, run: dict) -> None:
        tis = self.store.tis_for_run(run["dag_id"], run["run_id"])
        if not tis or any(t["state"] not in TI.TERMINAL for t in tis):
            return
        state = (
            DagRunState.SUCCESS
            if all(t["state"] in (TI.SUCCESS, TI.SKIPPED) for t in tis)
            else DagRunState.FAILED
        )
        self.store.set_run_state(run["dag_id"], run["run_id"], state)

    # -- main loop -----------------------------------------------------------------

    def run(
        self,
        num_runs: int | None = None,
        poll_interval: float = 0.1,
        timeout: float = 300.0,
    ) -> dict:
        """Loop ticks until every created run is terminal.

        ``num_runs`` additionally waits until each scheduled DAG has created at
        least that many *scheduled* runs (manual/backfill runs are not counted).
        """
        deadline = time.monotonic() + timeout
        while True:
            self.tick()
            if self._is_done(num_runs):
                break
            if time.monotonic() > deadline:
                raise TimeoutError("scheduler did not become idle before the timeout")
            time.sleep(poll_interval)
        self.executor.shutdown()
        return {"state": "idle", "runs": len(self.store.active_runs())}

    def _is_done(self, num_runs: int | None) -> bool:
        if self.store.active_runs() or not self.executor.is_idle():
            return False
        if num_runs is None:
            return True
        for dag in self.bag().dags.values():
            if dag.schedule is None:
                continue
            if len(self.store.runs_for_dag(dag.dag_id, run_type="scheduled")) < num_runs:
                return False
        return True


def _parse_stored(value: str) -> datetime:
    return datetime.fromisoformat(value)
