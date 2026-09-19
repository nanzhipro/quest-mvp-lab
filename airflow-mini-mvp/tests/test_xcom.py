"""XCom: auto-push of return values and pull via context["ti"]."""
from miniflow import DAG, PythonOperator
from miniflow.models import DagRunState, XCOM_RETURN_KEY
from miniflow.store import utcnow


def _run_dag(store, scheduler_for, dag):
    run_id = f"manual__{utcnow().isoformat()}"
    store.create_dag_run(dag.dag_id, run_id, utcnow(), run_type="manual", state=DagRunState.QUEUED)
    scheduler_for([dag]).run(timeout=30)
    return run_id


def test_return_value_auto_pushed(store, scheduler_for):
    with DAG("xc_push", schedule=None) as dag:
        @dag.task
        def produce():
            return {"answer": 42}

        produce()
    run_id = _run_dag(store, scheduler_for, dag)
    assert store.xcom_get("xc_push", run_id, "produce", XCOM_RETURN_KEY) == {"answer": 42}


def test_xcom_pull_single_and_list(store, scheduler_for):
    seen = {}

    with DAG("xc_pull", schedule=None) as dag:
        @dag.task
        def a():
            return 1

        @dag.task
        def b():
            return 2

        @dag.task
        def c(ti):
            seen["single"] = ti.xcom_pull(task_ids="a")
            seen["list"] = ti.xcom_pull(task_ids=["a", "b"])

        [a(), b()] >> c()
    _run_dag(store, scheduler_for, dag)
    assert seen["single"] == 1
    assert seen["list"] == [1, 2]


def test_xcom_pull_missing_returns_none(store, scheduler_for):
    seen = {}

    with DAG("xc_missing", schedule=None) as dag:
        @dag.task
        def a():
            return 1

        @dag.task
        def c(ti):
            seen["v"] = ti.xcom_pull(task_ids="nonexistent")

        a() >> c()
    _run_dag(store, scheduler_for, dag)
    assert seen["v"] is None


def test_none_return_pushes_no_xcom(store, scheduler_for):
    with DAG("xc_none", schedule=None) as dag:
        PythonOperator(task_id="quiet", python_callable=lambda: None)
    run_id = _run_dag(store, scheduler_for, dag)
    assert store.xcoms_for_run("xc_none", run_id) == []


def test_non_json_serializable_falls_back_to_str(store, scheduler_for):
    with DAG("xc_str", schedule=None) as dag:
        @dag.task
        def produce():
            return {1, 2, 3}

        produce()
    run_id = _run_dag(store, scheduler_for, dag)
    value = store.xcom_get("xc_str", run_id, "produce", XCOM_RETURN_KEY)
    assert isinstance(value, str) and "1" in value
