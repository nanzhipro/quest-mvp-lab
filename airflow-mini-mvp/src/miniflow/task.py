"""Task classes and dependency operators.

Mirrors:
- ``Task``           -> ``airflow.models.BaseOperator`` (dependency ops ``>>``/``<<``,
  ``retries`` / ``retry_delay`` attributes)
- ``PythonOperator`` -> ``airflow.operators.python.PythonOperator``
- ``DecoratedTask``  -> TaskFlow ``@task`` (``airflow.decorators.python``)
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .dag import DAG

DEFAULT_RETRY_DELAY = timedelta(minutes=5)


def _call_with_context(fn, context: dict, extra_kwargs: dict | None = None):
    """Call ``fn`` injecting context params it declares (Airflow context kwargs).

    A function declaring ``ti``, ``context``, ``logical_date`` etc. receives
    them automatically; ``**kwargs`` functions receive the whole context.
    """
    params = inspect.signature(fn).parameters
    accepts_var = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    injected = dict(context) if accepts_var else {k: v for k, v in context.items() if k in params}
    injected.update(extra_kwargs or {})
    return fn(**injected)


def _flatten(tasks) -> list[Task]:
    """Accept a Task or an iterable of Tasks (Airflow's list wiring support)."""
    if isinstance(tasks, Task):
        return [tasks]
    out: list[Task] = []
    for t in tasks:
        out.extend(_flatten(t))
    return out


class Task:
    """Base task. Subclasses implement :meth:`execute`."""

    def __init__(
        self,
        task_id: str,
        dag: DAG | None = None,
        retries: int = 0,
        retry_delay: timedelta = DEFAULT_RETRY_DELAY,
    ):
        from .dag import DagContext

        self.task_id = task_id
        self.retries = retries
        self.retry_delay = retry_delay
        self.upstream_task_ids: set[str] = set()
        self.downstream_task_ids: set[str] = set()
        self.dag: DAG | None = None
        owner = dag or DagContext.current()
        if owner is None:
            raise ValueError(f"task {task_id!r} needs a dag (or a `with DAG(...)` context)")
        owner.add_task(self)

    # -- dependency operators: a >> b means "b runs after a" ------------------

    def __rshift__(self, other):
        for t in _flatten(other):
            self.dag.add_edge(self.task_id, t.task_id)
        return other

    def __lshift__(self, other):
        for t in _flatten(other):
            self.dag.add_edge(t.task_id, self.task_id)
        return other

    def __rrshift__(self, other):  # [a, b] >> self
        for t in _flatten(other):
            self.dag.add_edge(t.task_id, self.task_id)
        return self

    def __rlshift__(self, other):  # [a, b] << self
        for t in _flatten(other):
            self.dag.add_edge(self.task_id, t.task_id)
        return self

    # -- execution -----------------------------------------------------------

    def execute(self, context: dict):
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<Task: {self.task_id}>"


class PythonOperator(Task):
    """Classic operator: wraps a plain callable. Mirrors Airflow's PythonOperator.

    Like Airflow, callables that declare context parameter names (``ti``,
    ``logical_date``, ...) receive them automatically.
    """

    def __init__(self, task_id: str, python_callable: Callable, op_kwargs: dict | None = None, **kwargs):
        super().__init__(task_id=task_id, **kwargs)
        self.python_callable = python_callable
        self.op_kwargs = op_kwargs or {}

    def execute(self, context: dict):
        return _call_with_context(self.python_callable, context, self.op_kwargs)


class DecoratedTask(Task):
    """TaskFlow-style task from ``@dag.task``.

    The wrapped function may declare a ``ti`` or ``context`` parameter; those
    are injected from the execution context (Airflow's "context kwargs").
    """

    def __init__(self, task_id: str, python_callable: Callable, **kwargs):
        super().__init__(task_id=task_id, **kwargs)
        self.python_callable = python_callable

    def execute(self, context: dict):
        return _call_with_context(self.python_callable, context)
