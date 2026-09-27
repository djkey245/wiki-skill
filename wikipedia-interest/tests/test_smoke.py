import wikitrends


def test_package_importable_and_versioned():
    assert wikitrends.VERSION == "0.1.0"
    assert wikitrends.DEFAULT_CONTACT.startswith("https://")
