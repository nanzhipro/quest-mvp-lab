import textwrap

import pytest

from miniflow import DAG, MetadataStore
from miniflow.loading import DagBag
from miniflow.scheduler import Scheduler
from miniflow.store import utcnow


@pytest.fixture
def store(tmp_path):
    s = MetadataStore(str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture
def make_dag():
    """DAG factory with a unique id per call."""
    n = {"i": 0}

    def _make(**kwargs):
        n["i"] += 1
        kwargs.setdefault("dag_id", f"test_dag_{n['i']}")
        return DAG(**kwargs)

    return _make


@pytest.fixture
def scheduler_for(store):
    """Build a Scheduler over an in-memory DagBag (no dags folder needed)."""

    def _build(dags, executor=None, now_fn=utcnow):
        sched = Scheduler(store, dags_folder="/nonexistent", executor=executor, now_fn=now_fn)
        sched._bag = DagBag(dags={d.dag_id: d for d in dags})
        return sched

    return _build


@pytest.fixture
def dags_folder(tmp_path):
    folder = tmp_path / "dags"
    folder.mkdir()

    def _write(name: str, source: str):
        (folder / name).write_text(textwrap.dedent(source))
        return folder

    return _write
