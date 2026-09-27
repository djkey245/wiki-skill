#!/usr/bin/env python3
"""Agent evals of the skill: python3 evals/run.py --help (testing §11)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wteval.run import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
