import pytest

from wikifake import SITEMATRIX, client, reply
from wikitrends import sitematrix
from wikitrends.cache import Cache
from wikitrends.errors import WikitrendsError


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path / "cache.sqlite")


def matrix_handler(request):
    if request.params.get("action") == "sitematrix":
        return reply(SITEMATRIX)
    return None


def test_parse_keeps_open_wikipedias_by_subdomain():
    sites = sitematrix.parse_sitematrix(SITEMATRIX)
    assert sorted(sites) == ["be-tarask", "no", "pl", "simple", "uk", "zh-yue"]
    assert sites["be-tarask"].dbname == "be_x_oldwiki"
    assert sites["uk"].project == "uk.wikipedia"
    assert sites["uk"].api == "https://uk.wikipedia.org/w/api.php"


@pytest.mark.parametrize(
    "code, subdomain",
    [("uk", "uk"), ("PL", "pl"), ("nb", "no"), ("be-x-old", "be-tarask"), ("be_tarask", "be-tarask"),
     ("yue", "zh-yue"), ("simple", "simple")],
)
def test_resolve_with_aliases(cache, code, subdomain):
    http, _ = client(matrix_handler)
    assert sitematrix.SiteMatrix.load(http, cache).resolve(code).code == subdomain


def test_unknown_code_suggests_similar(cache):
    http, _ = client(matrix_handler)
    matrix = sitematrix.SiteMatrix.load(http, cache)
    with pytest.raises(WikitrendsError) as info:
        matrix.resolve("ukr")
    assert info.value.code == "unknown_lang"
    assert "uk" in info.value.details["similar"]
    with pytest.raises(WikitrendsError):
        matrix.resolve("aa")  # closed wiki


def test_sitematrix_and_main_page_are_cached(cache):
    def handler(request):
        if request.params.get("meta") == "siteinfo":
            assert request.host == "uk.wikipedia.org"
            return reply({"batchcomplete": True, "query": {"general": {"mainpage": "Головна сторінка", "sitename": "Вікіпедія"}}})
        return matrix_handler(request)

    http, transport = client(handler)
    site = sitematrix.SiteMatrix.load(http, cache).resolve("uk")
    sitematrix.SiteMatrix.load(http, cache)
    assert sitematrix.main_page(http, cache, site) == "Головна сторінка"
    assert sitematrix.main_page(http, cache, site) == "Головна сторінка"
    assert transport.count() == 2
    assert transport.requests[0].params["formatversion"] == "2"
