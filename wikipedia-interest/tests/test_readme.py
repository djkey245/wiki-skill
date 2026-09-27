"""README.md links and required content (Plan 11 Task 3)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def test_readme_links_exist():
    text = README.read_text(encoding="utf-8")
    links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text)
    assert links
    for label, target in links:
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path_str = target.split("#")[0]
        if not path_str:
            continue
        target_path = (ROOT / path_str).resolve()
        assert target_path.exists(), f"Broken link in README.md: {target} (resolved to {target_path})"


def test_readme_required_phrases_and_omissions():
    text = README.read_text(encoding="utf-8")
    for phrase in (
        "cache info",
        "cache clear",
        "uv sync --dev",
        "uv run pytest",
        "Roadmap",
        "How AI was used",
        "MB",
        "not a market",
    ):
        assert phrase in text, f"Missing required phrase in README: {phrase}"
    for forbidden in ("docs/design", "docs/superpowers"):
        assert forbidden not in text, f"Forbidden reference found in README: {forbidden}"
