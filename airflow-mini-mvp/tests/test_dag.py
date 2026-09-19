"""DAG wiring, TaskFlow decorator, dependency operators, cycle detection."""
import pytest

from miniflow import DAG, DagCycleError, DuplicateTaskIdError, PythonOperator


def noop():
    return None


def test_context_manager_owns_tasks(make_dag):
    with make_dag() as dag:
        t = PythonOperator(task_id="a", python_callable=noop)
    assert dag.tasks == {"a": t}
    assert t.dag is dag


def test_dag_task_decorator_bare(make_dag):
    with make_dag() as dag:
        @dag.task
        def extract():
            return 1

        t = extract()
    assert t.task_id == "extract"
    assert t.execute({}) == 1


def test_dag_task_decorator_with_args(make_dag):
    from datetime import timedelta

    with make_dag() as dag:
        @dag.task(retries=3, retry_delay=timedelta(seconds=1), task_id="custom")
        def extract():
            return 1

        t = extract()
    assert t.task_id == "custom"
    assert t.retries == 3
    assert t.retry_delay == timedelta(seconds=1)


def test_rshift_sets_dependencies(make_dag):
    with make_dag() as dag:
        a = PythonOperator(task_id="a", python_callable=noop)
        b = PythonOperator(task_id="b", python_callable=noop)
        a >> b
    assert b.task_id in a.downstream_task_ids
    assert a.task_id in b.upstream_task_ids


def test_lshift_sets_dependencies(make_dag):
    with make_dag() as dag:
        a = PythonOperator(task_id="a", python_callable=noop)
        b = PythonOperator(task_id="b", python_callable=noop)
        a << b
    assert a.task_id in b.downstream_task_ids
    assert b.task_id in a.upstream_task_ids


def test_list_wiring(make_dag):
    with make_dag() as dag:
        a = PythonOperator(task_id="a", python_callable=noop)
        b = PythonOperator(task_id="b", python_callable=noop)
        c = PythonOperator(task_id="c", python_callable=noop)
        [a, b] >> c
    assert c.upstream_task_ids == {"a", "b"}


def test_chained_wiring(make_dag):
    with make_dag() as dag:
        a = PythonOperator(task_id="a", python_callable=noop)
        b = PythonOperator(task_id="b", python_callable=noop)
        c = PythonOperator(task_id="c", python_callable=noop)
        a >> b >> c
    assert b.upstream_task_ids == {"a"}
    assert c.upstream_task_ids == {"b"}


def test_cycle_detection_raises(make_dag):
    with make_dag() as dag:
        a = PythonOperator(task_id="a", python_callable=noop)
        b = PythonOperator(task_id="b", python_callable=noop)
        c = PythonOperator(task_id="c", python_callable=noop)
        a >> b >> c
        with pytest.raises(DagCycleError):
            c >> a


def test_self_cycle_raises(make_dag):
    with make_dag() as dag:
        a = PythonOperator(task_id="a", python_callable=noop)
        with pytest.raises(DagCycleError):
            a >> a


def test_duplicate_task_id_raises(make_dag):
    with make_dag() as dag:
        PythonOperator(task_id="a", python_callable=noop)
        with pytest.raises(DuplicateTaskIdError):
            PythonOperator(task_id="a", python_callable=noop)


def test_topo_sort_respects_dependencies(make_dag):
    with make_dag() as dag:
        a = PythonOperator(task_id="a", python_callable=noop)
        b = PythonOperator(task_id="b", python_callable=noop)
        c = PythonOperator(task_id="c", python_callable=noop)
        [a, b] >> c
    order = [t.task_id for t in dag.topo_sort()]
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("c")


def test_task_outside_context_needs_dag(make_dag):
    dag = make_dag()
    t = PythonOperator(task_id="a", python_callable=noop, dag=dag)
    assert t.dag is dag
