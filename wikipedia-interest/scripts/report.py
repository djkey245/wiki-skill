#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "matplotlib==3.9.4; python_version < '3.10'",
#     "matplotlib==3.10.9; python_version >= '3.10'",
# ]
# ///
"""Report entry point: uv run <skill_dir>/scripts/report.py <spec.json | result.json> [--format pdf,png,md]
[--topic <id>] [--lang xx] [--out <dir>]. Without uv it works under python3 when matplotlib is installed."""
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MATPLOTLIB = "3.9.4" if sys.version_info < (3, 10) else "3.10.9"  # the pins of the header above


def deps_missing(argv):
    from wikitrends.errors import WikitrendsError

    script = os.path.abspath(__file__)
    rerun = " ".join(shlex.quote(a) for a in ["uv", "run", script, *argv])
    error = WikitrendsError(
        "report_deps_missing", "The report needs matplotlib, which is not installed",
        hint=f"Run: {rerun} (uv: https://docs.astral.sh/uv/getting-started/installation/), "
             f"or install matplotlib: python3 -m pip install matplotlib=={MATPLOTLIB}",
    )
    payload = {**error.to_payload(), "schema_version": 1}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return error.exit_code


def main():
    argv = sys.argv[1:]
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return deps_missing(argv)
    from wikitrends import render

    return render.run(argv, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
