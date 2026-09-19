"""State machine persistence and scheduler dependency gating."""
from datetime import datetime, timedelta

from miniflow import DAG, PythonOperator
from miniflow.models import DagRunState, TaskInstanceState as TI
from miniflow.scheduler import Scheduler
from miniflow.store import utcnow


def _two_task_dag(dag_id="gating", fail_first=False, start=None):
    calls = []

    def first(**_):
        calls.append("first")
        if fail_first:
            raise RuntimeError("boom")
        return 1

    def second(**_):
        calls.append("second")
        return 2

    dag = DAG(dag_id, schedule=None, start_date=start)
    with dag:
        a = PythonOperator(task_id="first", python_callable=first)
        b = PythonOperator(task_id="second", python_callable=second)
        a >> b
    return dag, calls


def _trigger(store, dag):
    now = utcnow()
    run_id = f"manual__{now.isoformat()}"
    store.create_dag_run(dag.dag_id, run_id, now, run_type="manual", state=DagRunState.QUEUED)
    return run_id


def test_ti_states_persisted_through_lifecycle(store, scheduler_for):
    dag, _ = _two_task_dag()
    run_id = _trigger(store, dag)
    sched = scheduler_for([dag])
    sched.tick()
    ti = store.get_ti(dag.dag_id, run_id, "first")
    assert ti["state"] == TI.SUCCESS  # SequentialExecutor ran it inline
    assert ti["try_number"] == 1
    assert ti["started_at"] and ti["ended_at"] and ti["queued_at"]


def test_downstream_not_queued_until_upstream_success(store, scheduler_for):
    dag, _ = _two_task_dag()
    run_id = _trigger(store, dag)
    sched = scheduler_for([dag])
    sched.tick()
    # After one pass: upstream succeeded, downstream still waiting.
    assert store.get_ti(dag.dag_id, run_id, "second")["state"] == TI.NONE
    sched.tick()
    assert store.get_ti(dag.dag_id, run_id, "second")["state"] == TI.SUCCESS


def test_run_reaches_success_via_run_loop(store, scheduler_for):
    dag, calls = _two_task_dag()
    _trigger(store, dag)
    scheduler_for([dag]).run(timeout=30)
    assert calls == ["first", "second"]
    run = store.runs_for_dag(dag.dag_id)[0]
    assert run["state"] == DagRunState.SUCCESS


def test_upstream_failure_marks_downstream_upstream_failed(store, scheduler_for):
    dag, _ = _two_task_dag(fail_first=True)
    run_id = _trigger(store, dag)
    scheduler_for([dag]).run(timeout=30)
    assert store.get_ti(dag.dag_id, run_id, "first")["state"] == TI.FAILED
    assert store.get_ti(dag.dag_id, run_id, "second")["state"] == TI.UPSTREAM_FAILED
    assert store.runs_for_dag(dag.dag_id)[0]["state"] == DagRunState.FAILED


def test_local_executor_runs_to_success(store, scheduler_for):
    from miniflow.executors import LocalExecutor

    dag, calls = _two_task_dag()
    _trigger(store, dag)
    ex = LocalExecutor(parallelism=2)
    scheduler_for([dag], executor=ex).run(timeout=30)
    ex.shutdown()
    assert calls == ["first", "second"]
    assert store.runs_for_dag(dag.dag_id)[0]["state"] == DagRunState.SUCCESS
