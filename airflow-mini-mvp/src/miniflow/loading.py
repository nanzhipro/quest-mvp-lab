"""DAG file loading.

Mirrors Airflow's DagFileProcessor / DagBag at toy scale: every ``.py`` file in
the dags folder is imported as its own module, and module-level ``DAG``
instances are collected into a DagBag with per-file import errors.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .dag import DAG

_module_counter = itertools.count()


@dataclass
class DagBag:
    dags: dict[str, DAG] = field(default_factory=dict)
    import_errors: dict[str, str] = field(default_factory=dict)

    def get_dag(self, dag_id: str) -> DAG | None:
        return self.dags.get(dag_id)


def load_dags(dags_folder: str | Path) -> DagBag:
    """Import every .py file under ``dags_folder`` and collect DAG objects."""
    folder = Path(dags_folder)
    bag = DagBag()
    if not folder.is_dir():
        bag.import_errors[str(folder)] = "dags folder does not exist"
        return bag
    for path in sorted(folder.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"miniflow_dagfile_{path.stem}_{next(_module_counter)}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception:
            bag.import_errors[str(path)] = traceback.format_exc()
            continue
        for value in vars(module).values():
            if isinstance(value, DAG):
                value.fileloc = str(path)
                bag.dags[value.dag_id] = value
    return bag
