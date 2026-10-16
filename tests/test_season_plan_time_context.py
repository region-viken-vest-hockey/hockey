from tournament_scheduler.html.templates import (
    FILTERS,
    HEATMAP,
    SCHEDULE_JAVASCRIPT,
    SHARED_JAVASCRIPT,
)


def test_upcoming_filter_is_enabled_by_default():
    assert 'id="filterUpcoming" checked' in FILTERS
    assert "Kun kommende" in FILTERS
    assert "t.d >= localIsoDate()" in SCHEDULE_JAVASCRIPT


def test_upcoming_filter_keeps_today_visible():
    # The comparison is inclusive: tournaments dated today are upcoming.
    assert "t.d >= localIsoDate()" in SCHEDULE_JAVASCRIPT


def test_heatmap_marks_today_and_dims_past_weeks():
    assert "isPast = sundayMs < todayUtc" in SHARED_JAVASCRIPT
    assert "isCurrent = mondayMs <= todayUtc && todayUtc <= sundayMs" in SHARED_JAVASCRIPT
    assert "var(--amber)" in SHARED_JAVASCRIPT
    assert "grayscale(0.7)" in SHARED_JAVASCRIPT


def test_page_fragments_do_not_precede_the_generated_page_script():
    assert "<script" not in FILTERS
    assert "<script" not in HEATMAP
