"""Executors.

Mirrors:
- ``SequentialExecutor`` -> ``airflow.executors.sequential_executor`` (default,
  runs one task at a time, in-process, blocking).
- ``LocalExecutor``      -> ``airflow.executors.local_executor`` (parallelism via
  a pool of worker threads; Airflow uses processes — same scheduling semantics).
"""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor


class SequentialExecutor:
    """Default executor: runs each job inline, one at a time."""

    name = "SequentialExecutor"

    def execute(self, job) -> None:
        job.run()

    def sync(self) -> None:
        pass

    def is_idle(self) -> bool:
        return True

    def shutdown(self) -> None:
        pass


class LocalExecutor:
    """Thread-pool executor: runs up to ``parallelism`` jobs concurrently."""

    name = "LocalExecutor"

    def __init__(self, parallelism: int = 4):
        self.parallelism = parallelism
        self._pool = ThreadPoolExecutor(max_workers=parallelism)
        self._futures: list[Future] = []
        self._lock = threading.Lock()

    def execute(self, job) -> None:
        with self._lock:
            self._futures.append(self._pool.submit(job.run))

    def sync(self) -> None:
        """Reap finished jobs (job.run() captures its own exceptions)."""
        with self._lock:
            done = [f for f in self._futures if f.done()]
            for f in done:
                f.result()  # surface unexpected executor-level crashes
                self._futures.remove(f)

    def is_idle(self) -> bool:
        with self._lock:
            return all(f.done() for f in self._futures)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)
