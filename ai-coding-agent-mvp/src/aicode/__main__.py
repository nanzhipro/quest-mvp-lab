"""Allow running the CLI as ``python -m aicode``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
