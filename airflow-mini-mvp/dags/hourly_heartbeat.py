"""Example scheduled DAG with catchup.

Airflow concepts demonstrated: ``schedule=timedelta(...)``, ``start_date``, and
``catchup=True`` — since start_date is 3 intervals in the past, the scheduler
creates one DagRun per missed interval, oldest first. Flip ``catchup`` to
False and only the latest interval is scheduled.

    miniflow scheduler --num-runs 3
    miniflow dags state hourly_heartbeat <run_id>
"""
from datetime import timedelta

from miniflow import DAG, utcnow

with DAG(
    "hourly_heartbeat",
    schedule=timedelta(hours=1),
    # All miniflow timestamps are naive UTC; use utcnow() (not datetime.now())
    # for relative start dates.
    start_date=utcnow() - timedelta(hours=3),
    catchup=True,
) as dag:

    @dag.task
    def heartbeat(logical_date=None):
        print(f"heartbeat for data interval starting at {logical_date}")
        return {"logical_date": str(logical_date)}

    heartbeat()
