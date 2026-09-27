import pkgutil
import subprocess
import sys
from pathlib import Path

import wikitrends

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FORBIDDEN = ("matplotlib", "numpy", "scipy", "pymannkendall")
# render.py is imported only by report.py (architecture §1); it is allowed to use matplotlib.
REPORT_ONLY = {"render"}


def core_modules():
    return sorted(
        f"wikitrends.{m.name}"
        for m in pkgutil.iter_modules(wikitrends.__path__)
        if m.name not in REPORT_ONLY
    )


def test_core_modules_do_not_import_heavy_dependencies():
    code = (
        "import sys, importlib\n"
        f"for name in {core_modules()!r}:\n"
        "    importlib.import_module(name)\n"
        f"bad = [m for m in {FORBIDDEN!r} if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(SCRIPTS), capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
