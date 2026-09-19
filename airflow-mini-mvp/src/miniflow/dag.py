"""DAG definition API.

Mirrors ``airflow.models.dag.DAG``:
- ``with DAG("my_dag", schedule=..., start_date=..., catchup=...) as dag:``
  context manager that makes the DAG the implicit owner of tasks defined inside.
- ``@dag.task`` TaskFlow-style decorator (with optional ``retries=`` /
  ``retry_delay=`` / ``task_id=`` arguments).
- Dependency wiring with ``>>`` / ``<<`` (and lists), with cycle detection that
  raises :class:`~miniflow.models.DagCycleError`
  (Airflow: ``AirflowDagCycleException``).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from .models import DagCycleError, DuplicateTaskIdError
from .schedule import normalize_schedule

if TYPE_CHECKING:
    from .task import Task


class DagContext:
    """Stack of DAGs currently open via ``with DAG(...)`` (Airflow's DagContext)."""

    _stack: list[DAG] = []

    @classmethod
    def push(cls, dag: DAG) -> None:
        cls._stack.append(dag)

    @classmethod
    def pop(cls, dag: DAG) -> None:
        if not cls._stack or cls._stack[-1] is not dag:
            raise RuntimeError("DAG context stack mismatch")
        cls._stack.pop()

    @classmethod
    def current(cls) -> DAG | None:
        return cls._stack[-1] if cls._stack else None


class DAG:
    """A directed acyclic graph of tasks. Toy-scale mirror of ``airflow.DAG``."""

    def __init__(
        self,
        dag_id: str,
        schedule=None,
        start_date: datetime | None = None,
        catchup: bool = True,
    ):
        self.dag_id = dag_id
        self.schedule = normalize_schedule(schedule)
        self.start_date = start_date
        self.catchup = catchup
        self.tasks: dict[str, Task] = {}
        self.fileloc: str = ""

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> DAG:
        DagContext.push(self)
        return self

    def __exit__(self, *exc) -> None:
        DagContext.pop(self)

    # -- tasks ---------------------------------------------------------------

    def add_task(self, task: Task) -> None:
        if task.task_id in self.tasks:
            raise DuplicateTaskIdError(
                f"task_id {task.task_id!r} already used in dag {self.dag_id!r}"
            )
        self.tasks[task.task_id] = task
        task.dag = self

    def task(self, _fn=None, **task_kwargs):
        """TaskFlow-style decorator: ``@dag.task`` or ``@dag.task(retries=2)``.

        The decorated function becomes a factory; calling it inside the DAG
        context materializes a task (task_id defaults to the function name).
        """

        def wrap(fn):
            def factory(**call_kwargs):
                from .task import DecoratedTask

                kwargs = {**task_kwargs, **call_kwargs}
                return DecoratedTask(
                    task_id=kwargs.pop("task_id", None) or fn.__name__,
                    python_callable=fn,
                    dag=DagContext.current() or self,
                    **kwargs,
                )

            factory.__name__ = fn.__name__
            factory._miniflow_dag = self
            return factory

        return wrap(_fn) if _fn is not None else wrap

    # -- dependency wiring -----------------------------------------------------

    def add_edge(self, upstream_id: str, downstream_id: str) -> None:
        """Wire ``upstream_id >> downstream_id`` with cycle detection."""
        if upstream_id == downstream_id or self._has_path(downstream_id, upstream_id):
            raise DagCycleError(
                f"Cycle detected in DAG {self.dag_id!r}: {upstream_id} >> {downstream_id}"
            )
        self.tasks[upstream_id].downstream_task_ids.add(downstream_id)
        self.tasks[downstream_id].upstream_task_ids.add(upstream_id)

    def _has_path(self, start: str, target: str) -> bool:
        seen, frontier = set(), [start]
        while frontier:
            node = frontier.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            frontier.extend(self.tasks[node].downstream_task_ids)
        return False

    def roots(self) -> list[Task]:
        return [t for t in self.tasks.values() if not t.upstream_task_ids]

    def topo_sort(self) -> list[Task]:
        """Kahn's algorithm; guaranteed acyclic by add_edge cycle checks."""
        indegree = {tid: len(t.upstream_task_ids) for tid, t in self.tasks.items()}
        queue = [tid for tid, d in indegree.items() if d == 0]
        order = []
        while queue:
            tid = queue.pop()
            order.append(self.tasks[tid])
            for down in self.tasks[tid].downstream_task_ids:
                indegree[down] -= 1
                if indegree[down] == 0:
                    queue.append(down)
        return order

    def __repr__(self) -> str:
        return f"<DAG: {self.dag_id} ({len(self.tasks)} tasks)>"
