"""miniflow — a toy re-implementation of Apache Airflow's core concepts.

Public API mirrors Airflow's author-facing imports:
    from miniflow import DAG, PythonOperator   # ~ airflow.DAG / PythonOperator
    from miniflow import DagCycleError          # ~ AirflowDagCycleException
"""
from .dag import DAG, DagContext
from .models import (
    XCOM_RETURN_KEY,
    DagCycleError,
    DagRunState,
    DuplicateTaskIdError,
    TaskInstanceHandle,
    TaskInstanceState,
)
from .store import MetadataStore, utcnow
from .task import DecoratedTask, PythonOperator, Task

__all__ = [
    "DAG",
    "DagContext",
    "DagCycleError",
    "DagRunState",
    "DecoratedTask",
    "DuplicateTaskIdError",
    "MetadataStore",
    "PythonOperator",
    "Task",
    "TaskInstanceHandle",
    "TaskInstanceState",
    "XCOM_RETURN_KEY",
    "utcnow",
]
