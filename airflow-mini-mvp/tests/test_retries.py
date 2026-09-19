"""Retries / up_for_retry semantics."""
from datetime import datetime, timedelta

from miniflow import DAG, PythonOperator
from miniflow.models import DagRunState, TaskInstanceState as TI
from miniflow.store import utcnow


def _flaky_dag(dag_id, retries, retry_delay, failures):
    attempts = {"n": 0}

    def flaky(**_):
        attempts["n"] += 1
        if attempts["n"] <= failures:
            raise RuntimeError("boom")
        return "ok"

    with DAG(dag_id, schedule=None) as dag:
        PythonOperator(task_id="flaky", python_callable=flaky,
                       retries=retries, retry_delay=retry_delay)
    return dag, attempts


def _trigger(store, dag):
    run_id = f"manual__{utcnow().isoformat()}"
    store.create_dag_run(dag.dag_id, run_id, utcnow(), run_type="manual", state=DagRunState.QUEUED)
    return run_id


def test_retry_succeeds_after_up_for_retry(store, scheduler_for):
    dag, attempts = _flaky_dag("retry_ok", retries=2, retry_delay=timedelta(milliseconds=20), failures=2)
    run_id = _trigger(store, dag)
    scheduler_for([dag]).run(timeout=30)
    ti = store.get_ti("retry_ok", run_id, "flaky")
    assert ti["state"] == TI.SUCCESS
    assert ti["try_number"] == 3  # initial try + 2 retries
    assert attempts["n"] == 3


def test_failed_after_last_attempt(store, scheduler_for):
    dag, attempts = _flaky_dag("retry_fail", retries=1, retry_delay=timedelta(milliseconds=20), failures=99)
    run_id = _trigger(store, dag)
    scheduler_for([dag]).run(timeout=30)
    ti = store.get_ti("retry_fail", run_id, "flaky")
    assert ti["state"] == TI.FAILED
    assert ti["try_number"] == 2  # retries=1 -> max 2 tries
    assert store.runs_for_dag("retry_fail")[0]["state"] == DagRunState.FAILED


def test_up_for_retry_state_is_visible_mid_run(store, scheduler_for):
    dag, _ = _flaky_dag("retry_mid", retries=2, retry_delay=timedelta(hours=1), failures=1)
    run_id = _trigger(store, dag)
    scheduler_for([dag]).tick()
    ti = store.get_ti("retry_mid", run_id, "flaky")
    assert ti["state"] == TI.UP_FOR_RETRY
    assert ti["next_retry_at"] is not None


def test_retry_not_requeued_before_delay(store, scheduler_for):
    base = datetime(2026, 9, 8, 12, 0, 0)
    clock = {"now": base}
    dag, _ = _flaky_dag("retry_delay", retries=2, retry_delay=timedelta(minutes=5), failures=1)
    store.create_dag_run("retry_delay", "manual__r", base, run_type="manual", state=DagRunState.QUEUED)
    sched = scheduler_for([dag], now_fn=lambda: clock["now"])
    sched.tick()
    assert store.get_ti("retry_delay", "manual__r", "flaky")["state"] == TI.UP_FOR_RETRY
    sched.tick()  # 5-minute delay has not elapsed
    assert store.get_ti("retry_delay", "manual__r", "flaky")["state"] == TI.UP_FOR_RETRY
    clock["now"] = base + timedelta(minutes=6)
    sched.tick()
    assert store.get_ti("retry_delay", "manual__r", "flaky")["state"] == TI.SUCCESS
