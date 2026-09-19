"""Allow ``python -m miniflow ...`` in addition to the ``miniflow`` script."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
