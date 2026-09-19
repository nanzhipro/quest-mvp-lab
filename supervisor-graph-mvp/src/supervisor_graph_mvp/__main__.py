"""``python -m supervisor_graph_mvp`` — same entry point as the ``supervisor-graph`` script."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover - exercised through the CLI tests
    sys.exit(main())
