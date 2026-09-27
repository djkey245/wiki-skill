"""report.py without matplotlib and its dependency pins; runs in the core CI job too."""
import json
import re
import subprocess
import sys
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "scripts" / "report.py"


def test_deps_missing_without_matplotlib():
    code = ("import runpy, sys; sys.modules['matplotlib'] = None; "
            f"sys.argv = ['report.py', 'x.json']; runpy.run_path({str(REPORT)!r}, run_name='__main__')")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    out = json.loads(proc.stdout)
    assert proc.returncode == 5 and out["code"] == "report_deps_missing" and out["ok"] is False
    assert f"uv run {REPORT} x.json" in out["hint"] and "pip install matplotlib==" in out["hint"]


def test_header_pins_match_the_lock():
    header = REPORT.read_text()
    pins = set(re.findall(r'"matplotlib==([0-9.]+);', header))
    lock = REPORT.with_name("report.py.lock").read_text()
    locked = set(re.findall(r'name = "matplotlib"\nversion = "([0-9.]+)"', lock))
    assert pins and pins == locked
    old, new = sorted(pins, key=lambda v: tuple(map(int, v.split("."))))
    assert f'MATPLOTLIB = "{old}" if sys.version_info < (3, 10) else "{new}"' in header
