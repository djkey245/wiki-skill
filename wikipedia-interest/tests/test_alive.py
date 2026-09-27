from wikitrends import alive


def test_alive_from_first_month_over_threshold():
    assert alive.alive_from([0, 5, 29, 30, 100, 120]) == 3


def test_redirect_for_two_years_then_article_is_alive_from_the_switch():
    # a redirect: ~0 views with sporadic hits and long zero runs, then a real article
    redirect = [0, 0, 0, 40, 0, 0, 0, 2, 0, 0, 0, 0] * 2
    article = [300, 280, 320, 310] * 6
    assert alive.alive_from(redirect + article) == 24


def test_short_zero_gaps_do_not_reset():
    views = [100, 0, 0, 100, 100]
    assert alive.alive_from(views) == 0
    assert alive.alive_from([100, 0, 0, 0, 100, 100]) == 4


def test_renamed_article_is_alive_from_the_old_title():
    old_title = [200] * 20 + [0] * 16
    new_title = [0] * 20 + [210] * 16
    summed = [a + b for a, b in zip(old_title, new_title)]
    assert alive.alive_from(summed) == 0


def test_missing_months_count_as_zeros():
    assert alive.alive_from([100, None, None, None, 100]) == 4


def test_never_alive():
    assert alive.alive_from([0, 10, 20, 0]) is None


def test_status_alive_from_window_start():
    assert alive.status([50] * 60, "2020-01", 36) == alive.AliveStatus(True)


def test_status_late_start_for_a_new_article():
    views = [0] * 40 + [500] * 20
    assert alive.status(views, "2020-01", 36) == alive.AliveStatus(False, "late_start", "2023-05")


def test_status_below_threshold_for_an_existing_small_article():
    # existed all along but was read less than ALIVE_MIN_VIEWS times a month until the window
    views = [12] * 40 + [60] * 20
    assert alive.status(views, "2020-01", 36) == alive.AliveStatus(False, "below_alive_threshold")
    assert alive.status([5] * 60, "2020-01", 36) == alive.AliveStatus(False, "below_alive_threshold")
