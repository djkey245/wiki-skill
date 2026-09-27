import pytest

from wikitrends import budget
from wikitrends.errors import WikitrendsError


def test_metadata_requests_are_batched():
    assert budget.metadata_requests(120, 3, 2) == 3 * 3 * 2
    assert budget.metadata_requests(0, 3, 2) == 0


def test_check_limit_and_allow_large():
    budget.check(3000, limit=3000, allow_large=False)
    budget.check(9000, limit=3000, allow_large=True)
    with pytest.raises(WikitrendsError) as info:
        budget.check(3001, limit=3000, allow_large=False)
    assert info.value.code == "budget_exceeded" and info.value.exit_code == 2
    assert "--allow-large" in info.value.hint and info.value.details["estimate"] == 3001
