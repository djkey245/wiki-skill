import pytest

from wikitrends import months


def test_parse_valid():
    assert months.parse("2024-09") == (2024, 9)


@pytest.mark.parametrize("bad", ["2024-9", "2024-13", "2024-00", "24-09", "2024/09", ""])
def test_parse_invalid(bad):
    with pytest.raises(ValueError):
        months.parse(bad)


def test_add_crosses_year_boundaries():
    assert months.add("2024-11", 3) == "2025-02"
    assert months.add("2025-02", -3) == "2024-11"
    assert months.add("2024-01", 0) == "2024-01"


def test_diff_is_signed_month_distance():
    assert months.diff("2026-08", "2024-09") == 23
    assert months.diff("2024-09", "2026-08") == -23


def test_span_is_inclusive():
    result = months.span("2024-09", "2026-08")
    assert len(result) == 24
    assert result[0] == "2024-09"
    assert result[-1] == "2026-08"


def test_span_single_month():
    assert months.span("2025-01", "2025-01") == ["2025-01"]


def test_span_rejects_reversed_range():
    with pytest.raises(ValueError):
        months.span("2025-02", "2025-01")
