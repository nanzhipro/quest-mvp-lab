# airflow-mini-mvp

A minimal, educational re-implementation of **Apache Airflow's core concepts**
at toy scale, in pure-stdlib Python 3.11+. The `miniflow` package mirrors
Airflow's author API (DAG context manager, `@dag.task`, `>>` wiring), its
runtime model (DagRuns, TaskInstance state machine, retries, XCom), and its
`airflow` CLI — backed by a SQLite metadata database.

## Background

Apache Airflow is the de-facto standard for programmatic workflow orchestration:
you author DAGs (directed acyclic graphs) of tasks in Python, a scheduler
creates *DagRuns* per schedule, gates *TaskInstances* on upstream success, and
hands them to an executor. Its production surface is huge (web UI, Celery,
Kubernetes, sensors, plugins), which makes the *core mental model* hard to see.

**What this MVP validates:** Airflow's core mental model — *DAG definition +
schedule + state machine + executor + metadata DB* — can be reproduced
faithfully in under ~1k lines of dependency-free Python, with the same state
names, the same retry and catchup semantics, and the same CLI verbs. It is a
learning aid: every module docstring names the Airflow concept it mirrors.

## Key decisions

- **Stdlib-only runtime.** SQLite (`sqlite3`) is the metadata store; cron is a
  tiny built-in 5-field parser. Only `pytest` is a (dev) dependency.
- **SQLite metadata DB** (default `./miniflow.db`) holds `dags`, `dag_runs`,
  `task_instances`, `xcoms` — mirroring Airflow's metadata database, so any CLI
  process can inspect run history.
- **Toy-scale scope.** Deliberately **not** implemented, with the Airflow
  counterpart each would map to:
  - Web UI / REST API — Airflow's Flask/React webserver (`airflow ui`).
  - Sensors — Airflow's `BaseSensorOperator` poke/reschedule loop.
  - Pools — Airflow's concurrency-limiting resource pools.
  - Dynamic task mapping — Airflow's `@task.expand()` mapped TIs.
  - Celery/Kubernetes executors — distributed execution backends; this MVP
    ships `SequentialExecutor` and a thread-pool `LocalExecutor` only.
  - Trigger rules / SLAs / callbacks — only the default `all_success` rule.

## Concept mapping

| miniflow construct | Airflow 3.x counterpart |
| ------------------ | ----------------------- |
| `with DAG("id", schedule=..., start_date=..., catchup=...)` | `airflow.sdk.DAG` context manager |
| `@dag.task` decorator | TaskFlow API `@task` (`airflow.sdk.task`) |
| `PythonOperator(task_id=..., python_callable=...)` | `airflow.providers.standard.operators.python.PythonOperator` |
| `a >> b`, `a << b`, `[a, b] >> c` | `BaseOperator` dependency operators |
| `DagCycleError` | `airflow.exceptions.AirflowDagCycleException` |
| `DagRun` + `logical_date` + `run_type` | `airflow.models.DagRun` (`logical_date`, `scheduled`/`manual`/`backfill`) |
| TI states `none…upstream_failed` | `airflow.utils.state.TaskInstanceState` |
| `Scheduler.tick()` / `Scheduler.run()` | `airflow.jobs.scheduler_job.SchedulerJobRunner` |
| `SequentialExecutor` / `LocalExecutor` | `airflow.executors.sequential_executor` / `local_executor` |
| `retries=N`, `retry_delay`, `up_for_retry` | Task retry semantics (`try_number`, delayed requeue) |
| XCom auto-push of return values, `ti.xcom_pull(...)` | `XCOM_RETURN_KEY` (`"return_value"`), JSON XCom backend |
| `MetadataStore` (SQLite) | Airflow metadata database (`[database] sql_alchemy_conn`) |
| `miniflow dags list/trigger/state/backfill`, `tasks states-for-dag-run`, `scheduler` | the `airflow` CLI |
| `loading.load_dags()` folder import | `DagFileProcessor` / `DagBag` |

Scheduling follows Airflow's data-interval semantics: a run for
`logical_date = L` covers `[L, next_fire(L))` and becomes ready only when the
interval ends. With `catchup=True` every ready interval since `start_date` gets
a run; with `catchup=False` only the latest one does.

## Build / run / verify

Tooling uses [uv](https://docs.astral.sh/uv/) (Python 3.11+). If `uv` is not on
PATH, a plain venv works too: `python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'`,
then substitute `.venv/bin/python -m pytest` / `.venv/bin/miniflow` below.

```bash
cd quest-mvp-lab/airflow-mini-mvp
uv sync --extra dev        # create venv, install package + pytest
uv run pytest -q           # 49 tests
```

### CLI E2E walkthrough

Run in a scratch directory so the metadata DB lands outside the repo:

```bash
mkdir /tmp/miniflow-demo && cd /tmp/miniflow-demo
DAGS=/path/to/airflow-mini-mvp/dags
alias miniflow="uv run --project /path/to/airflow-mini-mvp miniflow"

# 1. Discover DAGs (DagFileProcessor mirror)
miniflow dags list --dags-folder $DAGS

# 2. Trigger the ETL DAG manually, then let the scheduler execute it
miniflow dags trigger etl_demo --dags-folder $DAGS
miniflow scheduler --dags-folder $DAGS            # exits when idle
miniflow tasks states-for-dag-run etl_demo <run_id>   # states + xcom values

# 3. Retries: flaky task fails twice (up_for_retry), succeeds on try 3
miniflow dags trigger flaky_retry_demo --dags-folder $DAGS
miniflow scheduler --dags-folder $DAGS
miniflow tasks states-for-dag-run flaky_retry_demo <run_id>   # try_number: 3

# 4. Catchup: hourly_heartbeat starts 3 intervals in the past -> 3 runs
miniflow scheduler --dags-folder $DAGS --num-runs 3

# 5. Backfill a historical range
miniflow dags backfill hourly_heartbeat \
    --start-date 2026-09-01T00:00:00 --end-date 2026-09-01T05:00:00 --dags-folder $DAGS
miniflow scheduler --dags-folder $DAGS
```

`miniflow scheduler` runs until all DagRuns reach a terminal state (with a
`--timeout` safety net); `--num-runs N` additionally waits until each scheduled
DAG has N scheduled runs. Use `--executor local` for the thread-pool
LocalExecutor.

## Project layout

```
airflow-mini-mvp/
├── dags/                    # example DAGs (ETL+XCom, retries, scheduled catchup)
├── src/miniflow/
│   ├── dag.py               # DAG context manager, @dag.task, cycle detection
│   ├── task.py              # Task / PythonOperator / DecoratedTask, >> / <<
│   ├── models.py            # TI & DagRun states, ti handle, DagCycleError
│   ├── schedule.py          # cron / timedelta / @once schedules
│   ├── store.py             # SQLite metadata store (+ XCom JSON values)
│   ├── scheduler.py         # scheduler loop, dependency gating, retries
│   ├── executors.py         # SequentialExecutor, LocalExecutor
│   ├── loading.py           # dags-folder loader (DagFileProcessor mirror)
│   └── cli.py               # the `miniflow` CLI
└── tests/                   # 49 pytest tests
```

## Conclusion

Verified: **49/49 pytest tests pass** (`uv run pytest -q`) covering DAG wiring &
cycle detection, the TI state machine, scheduler dependency gating, catchup
True/False, retries/`up_for_retry`, XCom push/pull, backfill, and CLI smoke
paths. Live E2E against `dags/` confirmed: `dags list` discovers all three
example DAGs, a manually triggered `etl_demo` run reaches `success` with XCom
values visible in `tasks states-for-dag-run`, `flaky_retry_demo` ends in
`success` after visible `up_for_retry` transitions (`try_number` 3), and the
catchup DAG produces one DagRun per missed hourly interval. Airflow's core
model survives the trip down to <1k lines of stdlib Python.
