#!/usr/bin/env python3
"""Core entry point of the wikipedia-interest skill: python3 <skill_dir>/scripts/wikitrends.py <command>."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wikitrends.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
