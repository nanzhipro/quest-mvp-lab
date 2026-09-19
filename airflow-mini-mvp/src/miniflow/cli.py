"""``miniflow`` command line.

Mirrors the ``airflow`` CLI at toy scale:
- ``miniflow dags list``                      -> ``airflow dags list``
- ``miniflow dags trigger <dag_id>``          -> ``airflow dags trigger``
- ``miniflow dags state <dag_id> <run_id>``   -> ``airflow dags state``
- ``miniflow dags backfill <dag_id> -s -e``   -> ``airflow dags backfill``
- ``miniflow tasks states-for-dag-run ...``   -> ``airflow tasks states-for-dag-run``
- ``miniflow scheduler``                      -> ``airflow scheduler``
"""
from __future__ import annotations

import argparse
import sys

from .executors import LocalExecutor
from .loading import load_dags
from .models import DagRunState
from .scheduler import BACKFILL_RUN_PREFIX, MANUAL_RUN_PREFIX, Scheduler
from .store import MetadataStore, parse_dt, utcnow


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)) for r in rows]
    return "\n".join([line, sep, *body])


def _err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def cmd_dags_list(args) -> int:
    bag = load_dags(args.dags_folder)
    for path, err in bag.import_errors.items():
        print(f"import error in {path}:\n{err}", file=sys.stderr)
    rows = [
        [dag.dag_id, repr(dag.schedule) if dag.schedule else "None", len(dag.tasks), dag.fileloc]
        for dag in sorted(bag.dags.values(), key=lambda d: d.dag_id)
    ]
    print(_table(["dag_id", "schedule", "tasks", "file"], rows) if rows else "no DAGs found")
    return 0 if not bag.import_errors else 1


def cmd_dags_trigger(args) -> int:
    bag = load_dags(args.dags_folder)
    dag = bag.get_dag(args.dag_id)
    if dag is None:
        return _err(f"dag {args.dag_id!r} not found in {args.dags_folder}")
    store = MetadataStore(args.db)
    now = utcnow()
    run_id = f"{MANUAL_RUN_PREFIX}{now.isoformat()}"
    store.create_dag_run(dag.dag_id, run_id, now, run_type="manual", state=DagRunState.QUEUED)
    print(f"triggered {dag.dag_id}: run_id={run_id} (state=queued; run `miniflow scheduler` to execute)")
    return 0


def cmd_dags_state(args) -> int:
    store = MetadataStore(args.db)
    run = store.get_dag_run(args.dag_id, args.run_id)
    if run is None:
        return _err(f"no run {args.run_id!r} for dag {args.dag_id!r}")
    print(_table(["dag_id", "run_id", "run_type", "logical_date", "state"],
                 [[run["dag_id"], run["run_id"], run["run_type"], run["logical_date"], run["state"]]]))
    return 0


def cmd_dags_backfill(args) -> int:
    bag = load_dags(args.dags_folder)
    dag = bag.get_dag(args.dag_id)
    if dag is None:
        return _err(f"dag {args.dag_id!r} not found in {args.dags_folder}")
    if dag.schedule is None:
        return _err(f"dag {args.dag_id!r} has no schedule to backfill")
    start, end = parse_dt(args.start_date), parse_dt(args.end_date)
    store = MetadataStore(args.db)
    created, t = 0, start
    while t <= end:
        run_id = f"{BACKFILL_RUN_PREFIX}{t.isoformat()}"
        store.create_dag_run(dag.dag_id, run_id, t, run_type="backfill", state=DagRunState.QUEUED)
        created += 1
        nxt = dag.schedule.next_after(t)
        if nxt is None or nxt <= t:
            break
        t = nxt
    print(f"created {created} backfill run(s) for {dag.dag_id} in [{start} .. {end}]")
    return 0


def cmd_tasks_states(args) -> int:
    store = MetadataStore(args.db)
    tis = store.tis_for_run(args.dag_id, args.run_id)
    if not tis:
        return _err(f"no task instances for {args.dag_id} / {args.run_id}")
    rows = [
        [t["task_id"], t["state"], t["try_number"], t["started_at"] or "-", t["ended_at"] or "-"]
        for t in tis
    ]
    print(_table(["task_id", "state", "try_number", "started_at", "ended_at"], rows))
    xcoms = store.xcoms_for_run(args.dag_id, args.run_id)
    if xcoms:
        print("\nxcom:")
        print(_table(["task_id", "key", "value"],
                     [[x["task_id"], x["key"], repr(x["value"])] for x in xcoms]))
    return 0


def cmd_scheduler(args) -> int:
    store = MetadataStore(args.db)
    executor = LocalExecutor(parallelism=args.parallelism) if args.executor == "local" else None
    scheduler = Scheduler(store, dags_folder=args.dags_folder, executor=executor)
    print(f"scheduler starting (dags_folder={args.dags_folder}, executor={scheduler.executor.name})")
    try:
        scheduler.run(num_runs=args.num_runs, timeout=args.timeout)
    except TimeoutError:
        return _err(f"scheduler timed out after {args.timeout}s with active runs remaining")
    print("scheduler idle: all dag runs reached a terminal state")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miniflow", description=__doc__.splitlines()[1].strip())
    parser.add_argument("--db", default="./miniflow.db", help="metadata DB path (default ./miniflow.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_dags_folder(p):
        p.add_argument("--dags-folder", default="./dags", help="folder of DAG files (default ./dags)")

    dags = sub.add_parser("dags").add_subparsers(dest="dags_command", required=True)
    with_dags_folder(dags.add_parser("list", help="list DAGs in the dags folder"))
    trigger = dags.add_parser("trigger", help="create a manual DagRun")
    trigger.add_argument("dag_id")
    with_dags_folder(trigger)
    state = dags.add_parser("state", help="show a DagRun's state")
    state.add_argument("dag_id")
    state.add_argument("run_id")
    backfill = dags.add_parser("backfill", help="create DagRuns for a historical range")
    backfill.add_argument("dag_id")
    backfill.add_argument("--start-date", required=True)
    backfill.add_argument("--end-date", required=True)
    with_dags_folder(backfill)

    tasks = sub.add_parser("tasks").add_subparsers(dest="tasks_command", required=True)
    states = tasks.add_parser("states-for-dag-run", help="show TaskInstance states for a run")
    states.add_argument("dag_id")
    states.add_argument("run_id")

    sched = sub.add_parser("scheduler", help="run the scheduler loop until idle")
    sched.add_argument("--num-runs", type=int, default=None,
                       help="wait until each scheduled DAG has N scheduled runs")
    sched.add_argument("--executor", choices=["sequential", "local"], default="sequential")
    sched.add_argument("--parallelism", type=int, default=4)
    sched.add_argument("--timeout", type=float, default=300.0)
    with_dags_folder(sched)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        ("dags", "list"): cmd_dags_list,
        ("dags", "trigger"): cmd_dags_trigger,
        ("dags", "state"): cmd_dags_state,
        ("dags", "backfill"): cmd_dags_backfill,
        ("tasks", "states-for-dag-run"): cmd_tasks_states,
        ("scheduler", None): cmd_scheduler,
    }
    key = (args.command, getattr(args, "dags_command", getattr(args, "tasks_command", None)))
    return handlers[key](args)


if __name__ == "__main__":
    sys.exit(main())
