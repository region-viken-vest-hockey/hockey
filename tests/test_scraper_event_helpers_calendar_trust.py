"""Calendar-trust tier tests for `_group_club_calendar_status` (issue #274).

A successful scrape is not, by itself, sufficient evidence for automatic
placement -- a club's registry entry can mark its source as untrustworthy
for that purpose (currently Tønsberg's BookUp generic/public data) without
the scrape itself being "unknown" (blocked/skipped/errored).
"""

from tournament_scheduler.pipeline.scraper_event_helpers import _group_club_calendar_status


def _source_result(name: str, **overrides) -> dict:
    result = {"name": name, "blocked": False, "skipped": False, "scraper_error": None}
    result.update(overrides)
    return result


class TestGroupClubCalendarStatusTrustTier:
    def test_untrusted_club_downgrades_a_successful_scrape(self):
        # Tønsberg's registry entry is trusted_for_auto_placement=False.
        status = _group_club_calendar_status([_source_result("Tønsberg")])
        assert status["Tønsberg"] == "untrusted"

    def test_trusted_club_successful_scrape_stays_known(self):
        status = _group_club_calendar_status([_source_result("Ringerike")])
        assert status["Ringerike"] == "known"

    def test_blocked_scrape_stays_unknown_even_for_untrusted_club(self):
        # A blocked/missing scrape is a distinct problem from proven
        # untrustworthy evidence -- don't relabel it "untrusted".
        status = _group_club_calendar_status([_source_result("Tønsberg", blocked=True)])
        assert status["Tønsberg"] == "unknown"
