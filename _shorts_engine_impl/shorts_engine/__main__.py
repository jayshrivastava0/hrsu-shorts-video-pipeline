"""
Entry point for python -m shorts_engine.

Calls main() and exits with the returned exit code.
"""
import sys

from shorts_engine.cli import main

if __name__ == "__main__":
    sys.exit(main())
