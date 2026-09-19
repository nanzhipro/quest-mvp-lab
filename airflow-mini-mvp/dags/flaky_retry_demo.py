"""Example retries DAG (unscheduled — trigger it manually).

Airflow concepts demonstrated: ``retries=N`` / ``retry_delay`` and the
``up_for_retry`` state. ``flaky`` fails on tries 1 and 2, then succeeds on
try 3; watch the ``try_number`` column in ``miniflow tasks states-for-dag-run``.
"""
from datetime import timedelta

from miniflow import DAG, PythonOperator

_ATTEMPTS = {"n": 0}


def _flaky_callable(ti=None):
    _ATTEMPTS["n"] += 1
    print(f"flaky attempt #{_ATTEMPTS['n']}")
    if _ATTEMPTS["n"] < 3:
        raise RuntimeError("flaky boom (simulated transient failure)")
    return "recovered"


def _report(ti=None):
    value = ti.xcom_pull(task_ids="flaky")
    print(f"downstream sees: {value}")
    return value


with DAG("flaky_retry_demo", schedule=None) as dag:
    flaky = PythonOperator(
        task_id="flaky",
        python_callable=_flaky_callable,
        retries=2,
        retry_delay=timedelta(milliseconds=50),
    )
    report = PythonOperator(task_id="report", python_callable=_report)
    flaky >> report
