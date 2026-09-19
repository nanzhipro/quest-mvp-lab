"""``python -m react_loop_mvp`` → the same CLI as the installed ``react-loop`` script."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
