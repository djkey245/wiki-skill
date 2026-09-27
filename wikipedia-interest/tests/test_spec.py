import json

import pytest

from wikitrends import spec
from wikitrends.errors import WikitrendsError


def topic(label="астрономія", seeds=("Q333",), items=("Q333", "Q525")):
    return spec.make_topic(label, list(seeds), list(items))


def base(**overrides):
    data = spec.new_spec("uk", ["uk", "pl", "cs"], [topic()])
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "text, fallback, expected",
    [
        ("астрономія", "", "astronomiya"),
        ("Вивчення англійської мови", "", "vyvchennya-anhliiskoyi-movy"),
        ("Łódź i Kraków", "", "lodz-i-krakow"),
        ("Příliš žluťoučký kůň", "", "prilis-zlutoucky-kun"),
        ("C++ / Python!", "", "c-python"),
        ("天文学", "Q333", "q333"),
        ("a" * 60, "", "a" * 40),
        ("word " * 10, "", "word-word-word-word-word-word-word-word"),
    ],
)
def test_slugify(text, fallback, expected):
    assert spec.slugify(text, fallback) == expected


def test_unique_suffix():
    assert spec.unique("astro", []) == "astro"
    assert spec.unique("astro", ["astro", "astro-2"]) == "astro-3"


def test_relative_period_and_too_short():
    assert spec.parse_relative("24m") == 24
    with pytest.raises(WikitrendsError) as info:
        spec.parse_relative("6m")
    assert info.value.code == "period_too_short" and info.value.exit_code == 2
    with pytest.raises(WikitrendsError) as info:
        spec.parse_relative("2y")
    assert info.value.code == "invalid_arguments"


def test_resolve_window_rules():
    assert spec.resolve_window({"relative": "24m"}, "2026-08") == ("2024-09", "2026-08")
    assert spec.resolve_window({"relative": "24m", "start": "2023-01", "end": "2024-12"}, "2026-08") == ("2023-01", "2024-12")
    assert spec.resolve_window({"start": "2023-01"}, "2026-08") == ("2023-01", "2026-08")
    with pytest.raises(WikitrendsError) as info:
        spec.resolve_window({"start": "2023-01", "end": "2026-09"}, "2026-08")
    assert info.value.code == "end_not_available" and info.value.details["latest"] == "2026-08"
    with pytest.raises(WikitrendsError) as info:
        spec.resolve_window({"start": "2026-01"}, "2026-08")
    assert info.value.code == "period_too_short"


def test_explicit_start_before_2015_07_is_clipped_with_warning():
    warnings = []
    assert spec.check_explicit("2014-01", "2016-12", warnings) == ("2015-07", "2016-12")
    assert [w["code"] for w in warnings] == ["start_clipped"]
    with pytest.raises(WikitrendsError) as info:
        spec.check_explicit("2024-01", "2024-06", [])
    assert info.value.code == "period_too_short"


def test_validate_fills_defaults_and_rejects_bad_specs():
    data = spec.validate(base())
    assert data["options"] == {"include_redirects": True, "max_redirects": 10, "metric": "share"}
    for broken, message in [
        (base(version=2), "version"),
        (base(langs=[]), "langs"),
        (base(langs=["uk", "uk"]), "langs"),
        (base(period={}), "period"),
        (base(topics=[{"id": "Bad Id", "label": "x", "seeds": ["Q1"], "items": ["Q1"]}]), "id"),
        (base(topics=[{"id": "x", "label": "x", "seeds": ["Q1"], "items": ["Q2"]}]), "seeds"),
        (base(topics=[{"id": "x", "label": "x", "seeds": ["q1"], "items": ["q1"]}]), "QIDs"),
    ]:
        with pytest.raises(WikitrendsError) as info:
            spec.validate(broken)
        assert info.value.code == "invalid_arguments" and message in info.value.message


def test_limits_exceeded():
    many = topic(items=[f"Q{i}" for i in range(1, 52)] + ["Q333"])
    with pytest.raises(WikitrendsError) as info:
        spec.validate(base(topics=[many]))
    assert info.value.code == "limits_exceeded"
    with pytest.raises(WikitrendsError):
        spec.validate(base(langs=[f"l{i}" for i in range(11)]))


def test_create_writes_atomically_with_slug_suffix(tmp_path):
    first = spec.create(tmp_path, base())
    second = spec.create(tmp_path, base())
    assert first == (tmp_path / "astronomiya" / "spec.json").resolve()
    assert second.parent.name == "astronomiya-2"
    assert json.loads(first.read_text())["topics"][0]["id"] == "astronomiya"
    assert spec.load(first) == spec.validate(base())


def test_load_missing_or_broken(tmp_path):
    with pytest.raises(WikitrendsError) as info:
        spec.load(tmp_path / "nope.json")
    assert info.value.code == "invalid_arguments" and "ls" in info.value.hint
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(WikitrendsError):
        spec.load(tmp_path / "bad.json")


def test_items_need_topic_when_several_topics():
    data = spec.validate(base())
    spec.add_items(data, None, ["Q7", "Q525"])
    assert data["topics"][0]["items"] == ["Q333", "Q525", "Q7"]
    spec.add_topic(data, "біологія", ["Q420"])
    with pytest.raises(WikitrendsError) as info:
        spec.add_items(data, None, ["Q8"])
    assert info.value.code == "topic_required" and info.value.details["topics"] == ["astronomiya", "biolohiya"]
    spec.drop_items(data, "astronomiya", ["Q7"])
    assert data["topics"][0]["items"] == ["Q333", "Q525"]
    with pytest.raises(WikitrendsError):
        spec.drop_items(data, "astronomiya", ["Q333"])  # the only seed
    spec.drop_topic(data, "biolohiya")
    with pytest.raises(WikitrendsError):
        spec.drop_topic(data, "astronomiya")


def test_topic_ids_stay_unique():
    data = spec.validate(base())
    assert spec.add_topic(data, "Астрономія", ["Q1"])["id"] == "astronomiya-2"


def test_langs_period_and_criteria():
    data = spec.validate(base())
    spec.add_lang(data, "sk")
    spec.drop_lang(data, "cs")
    assert data["langs"] == ["uk", "pl", "sk"]
    spec.set_explicit(data, "2023-01", "2024-12", [])
    assert data["period"] == {"start": "2023-01", "end": "2024-12"}
    with pytest.raises(WikitrendsError) as info:
        spec.refresh_window(data)
    assert "--period" in info.value.hint
    spec.set_relative(data, "36m")
    spec.refresh_window(data)
    assert data["period"] == {"relative": "36m"}
    spec.set_criteria(data, spec.parse_criteria("min_monthly_views=50000, min_growth_pct_yr=7.5"))
    assert data["options"]["criteria"] == {"min_monthly_views": 50000, "min_growth_pct_yr": 7.5}
    with pytest.raises(WikitrendsError):
        spec.parse_criteria("size_axis=level_ppm")  # slice 2
    with pytest.raises(WikitrendsError):
        spec.parse_criteria("min_monthly_views=lots")
    spec.clear_criteria(data)
    assert "criteria" not in data["options"]
    spec.validate(data)


def test_list_specs(tmp_path):
    path = spec.create(tmp_path, base())
    (path.parent / "result.json").write_text("{}")
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "spec.json").write_text("not json")
    listed = spec.list_specs(tmp_path)
    assert [s["slug"] for s in listed] == ["astronomiya"]
    assert listed[0]["spec"] == str(path) and listed[0]["last_analysis"].endswith("Z")
    assert listed[0]["topics"] == [{"id": "astronomiya", "label": "астрономія", "seeds": ["Q333"], "items": 2}]
    assert spec.list_specs(tmp_path / "missing") == []
