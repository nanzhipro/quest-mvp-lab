"""CLI smoke tests + DAG-file loading + backfill run creation."""
from datetime import datetime

from miniflow.cli import main
from miniflow.loading import load_dags
from miniflow.models import DagRunState
from miniflow.store import MetadataStore

ETL_DAG = """
from miniflow import DAG

with DAG("etl_demo", schedule=None) as dag:

    @dag.task
    def extract():
        return [2, 1, 2]

    @dag.task
    def summarize(ti):
        data = ti.xcom_pull(task_ids="extract")
        return {"count": len(data)}

    extract() >> summarize()
"""

SCHEDULED_DAG = """
from datetime import datetime, timedelta
from miniflow import DAG, PythonOperator

with DAG(
    "hourly",
    schedule=timedelta(hours=1),
    start_date=datetime(2026, 9, 8, 0, 0),
    catchup=False,
) as dag:
    PythonOperator(task_id="beat", python_callable=lambda: "ok")
"""

BROKEN_DAG = "this is not valid python ("


def test_load_dags_collects_module_level_dags(dags_folder):
    folder = dags_folder("etl.py", ETL_DAG)
    bag = load_dags(folder)
    assert "etl_demo" in bag.dags
    assert bag.dags["etl_demo"].fileloc.endswith("etl.py")
    assert not bag.import_errors


def test_load_dags_captures_import_errors(dags_folder):
    folder = dags_folder("broken.py", BROKEN_DAG)
    bag = load_dags(folder)
    assert not bag.dags
    assert len(bag.import_errors) == 1


def test_cli_dags_list(dags_folder, tmp_path, capsys):
    folder = dags_folder("etl.py", ETL_DAG)
    rc = main(["--db", str(tmp_path / "m.db"), "dags", "list", "--dags-folder", str(folder)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "etl_demo" in out


def test_cli_dags_list_reports_import_errors(dags_folder, tmp_path, capsys):
    folder = dags_folder("broken.py", BROKEN_DAG)
    rc = main(["--db", str(tmp_path / "m.db"), "dags", "list", "--dags-folder", str(folder)])
    assert rc == 1
    assert "import error" in capsys.readouterr().err


def test_cli_trigger_then_scheduler_then_state(dags_folder, tmp_path, capsys):
    folder = dags_folder("etl.py", ETL_DAG)
    db = str(tmp_path / "m.db")
    assert main(["--db", db, "dags", "trigger", "etl_demo", "--dags-folder", str(folder)]) == 0
    out = capsys.readouterr().out
    run_id = [w for w in out.split() if w.startswith("run_id=")][0].split("=", 1)[1]

    assert main(["--db", db, "scheduler", "--dags-folder", str(folder), "--timeout", "30"]) == 0

    assert main(["--db", db, "dags", "state", "etl_demo", run_id]) == 0
    assert "success" in capsys.readouterr().out

    assert main(["--db", db, "tasks", "states-for-dag-run", "etl_demo", run_id]) == 0
    out = capsys.readouterr().out
    assert "success" in out and "{'count': 3}" in out  # xcom values visible


def test_cli_trigger_unknown_dag_errors(dags_folder, tmp_path):
    folder = dags_folder("etl.py", ETL_DAG)
    rc = main(["--db", str(tmp_path / "m.db"), "dags", "trigger", "nope", "--dags-folder", str(folder)])
    assert rc == 1


def test_cli_backfill_creates_runs(dags_folder, tmp_path, capsys):
    folder = dags_folder("hourly.py", SCHEDULED_DAG)
    db = str(tmp_path / "m.db")
    rc = main([
        "--db", db, "dags", "backfill", "hourly",
        "--start-date", "2026-09-08T00:00:00", "--end-date", "2026-09-08T03:00:00",
        "--dags-folder", str(folder),
    ])
    assert rc == 0
    assert "4 backfill run(s)" in capsys.readouterr().out
    store = MetadataStore(db)
    runs = store.runs_for_dag("hourly", run_type="backfill")
    assert [r["logical_date"] for r in runs] == [
        "2026-09-08T00:00:00",
        "2026-09-08T01:00:00",
        "2026-09-08T02:00:00",
        "2026-09-08T03:00:00",
    ]
    store.close()


def test_cli_backfill_runs_execute_via_scheduler(dags_folder, tmp_path):
    folder = dags_folder("hourly.py", SCHEDULED_DAG)
    db = str(tmp_path / "m.db")
    main(["--db", db, "dags", "backfill", "hourly",
          "--start-date", "2026-09-08T00:00:00", "--end-date", "2026-09-08T01:00:00",
          "--dags-folder", str(folder)])
    # catchup=False in the DAG, but backfill runs are explicit and must run.
    assert main(["--db", db, "scheduler", "--dags-folder", str(folder), "--timeout", "30"]) == 0
    store = MetadataStore(db)
    states = {r["state"] for r in store.runs_for_dag("hourly", run_type="backfill")}
    store.close()
    assert states == {DagRunState.SUCCESS}


def test_cli_scheduler_num_runs(dags_folder, tmp_path):
    folder = dags_folder("hourly.py", SCHEDULED_DAG)
    db = str(tmp_path / "m.db")
    rc = main(["--db", db, "scheduler", "--dags-folder", str(folder),
               "--num-runs", "1", "--timeout", "30"])
    assert rc == 0
    store = MetadataStore(db)
    # catchup=False, start_date in the past -> exactly one scheduled run, succeeded.
    runs = store.runs_for_dag("hourly", run_type="scheduled")
    store.close()
    assert len(runs) == 1 and runs[0]["state"] == DagRunState.SUCCESS
