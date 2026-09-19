"""Example ETL DAG (unscheduled — trigger it manually).

Airflow concepts demonstrated: TaskFlow ``@dag.task``, ``>>`` wiring, and XCom
return-value passing (``extract -> transform -> load``).

    miniflow dags trigger etl_demo
    miniflow scheduler
    miniflow tasks states-for-dag-run etl_demo <run_id>
"""
from miniflow import DAG

with DAG("etl_demo", schedule=None) as dag:

    @dag.task
    def extract():
        # Pretend this reads a source system; return value is auto-pushed to XCom.
        return [3, 1, 4, 1, 5, 9, 2, 6]

    @dag.task
    def transform(ti):
        data = ti.xcom_pull(task_ids="extract")
        return sorted(set(data))

    @dag.task
    def load(ti):
        result = ti.xcom_pull(task_ids="transform")
        print(f"loaded rows: {result}")
        return {"rows_loaded": len(result)}

    extract() >> transform() >> load()
