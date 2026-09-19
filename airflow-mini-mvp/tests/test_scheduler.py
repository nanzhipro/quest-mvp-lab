"""Scheduling semantics: catchup=True/False, backfill, @once, manual runs."""
from datetime import datetime, timedelta

from miniflow import DAG, PythonOperator
from miniflow.models import DagRunState
from miniflow.store import utcnow


def _heartbeat_dag(dag_id, schedule, start_date, catchup):
    with DAG(dag_id, schedule=schedule, start_date=start_date, catchup=catchup) as dag:
        PythonOperator(task_id="beat", python_callable=lambda: "ok")
    return dag


def test_catchup_true_creates_all_missed_runs(store, scheduler_for):
    start = datetime(2026, 9, 8, 0, 0)
    dag = _heartbeat_dag("cu_true", timedelta(hours=1), start, catchup=True)
    # Fake "now" 3 hours after start -> intervals [0,1), [1,2), [2,3) are ready.
    sched = scheduler_for([dag], now_fn=lambda: start + timedelta(hours=3, minutes=1))
    sched.run(timeout=30)
    runs = store.runs_for_dag("cu_true", run_type="scheduled")
    assert [r["logical_date"] for r in runs] == [
        "2026-09-08T00:00:00",
        "2026-09-08T01:00:00",
        "2026-09-08T02:00:00",
    ]
    assert all(r["state"] == DagRunState.SUCCESS for r in runs)


def test_catchup_false_creates_only_latest_run(store, scheduler_for):
    start = datetime(2026, 9, 8, 0, 0)
    dag = _heartbeat_dag("cu_false", timedelta(hours=1), start, catchup=False)
    sched = scheduler_for([dag], now_fn=lambda: start + timedelta(hours=3, minutes=1))
    sched.run(timeout=30)
    runs = store.runs_for_dag("cu_false", run_type="scheduled")
    assert [r["logical_date"] for r in runs] == ["2026-09-08T02:00:00"]


def test_unscheduled_dag_creates_no_runs(store, scheduler_for):
    dag = _heartbeat_dag("nosched", None, None, True)
    scheduler_for([dag]).run(timeout=10)
    assert store.runs_for_dag("nosched") == []


def test_once_schedule_creates_single_run(store, scheduler_for):
    dag = _heartbeat_dag("once", "@once", datetime(2026, 9, 8), True)
    scheduler_for([dag]).run(timeout=10)
    runs = store.runs_for_dag("once", run_type="scheduled")
    assert len(runs) == 1
    scheduler_for([dag]).run(timeout=10)
    assert len(store.runs_for_dag("once", run_type="scheduled")) == 1  # idempotent


def test_num_runs_waits_for_scheduled_runs(store, scheduler_for):
    start = datetime(2026, 9, 8, 0, 0)
    dag = _heartbeat_dag("numruns", timedelta(hours=1), start, catchup=True)
    sched = scheduler_for([dag], now_fn=lambda: start + timedelta(hours=3, minutes=1))
    sched.run(num_runs=2, timeout=30)
    assert len(store.runs_for_dag("numruns", run_type="scheduled")) >= 2


def test_manual_trigger_run_is_executed(store, scheduler_for):
    dag = _heartbeat_dag("manual", None, None, True)
    store.create_dag_run(
        "manual", "manual__x", utcnow(), run_type="manual", state=DagRunState.QUEUED
    )
    scheduler_for([dag]).run(timeout=10)
    assert store.get_dag_run("manual", "manual__x")["state"] == DagRunState.SUCCESS
