from tournament_scheduler.html.templates import FILTERS, HEATMAP


def test_upcoming_filter_is_enabled_by_default():
    assert 'id="filterUpcoming" checked' in FILTERS
    assert 'Kun kommende' in FILTERS
    assert "t.d >= localIsoDate()" in FILTERS


def test_upcoming_filter_keeps_today_visible():
    # The comparison is inclusive: tournaments dated today are upcoming.
    assert "t.d >= localIsoDate()" in FILTERS


def test_heatmap_marks_today_and_dims_past_weeks():
    assert "isPast = sundayMs < todayUtc" in HEATMAP
    assert "isCurrent = mondayMs <= todayUtc && todayUtc <= sundayMs" in HEATMAP
    assert "var(--amber)" in HEATMAP
    assert "grayscale(0.7)" in HEATMAP
