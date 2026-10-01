"""HTML export for the season plan — interactive overview page."""

# Canonical filename of the dedicated cancelled-tournament page. The exporter
# writes it next to the season plan and the export-parity reader reads it back,
# so the two layers share one owner for the artifact name instead of each
# reconstructing it.
CANCELLED_TOURNAMENTS_FILENAME = "cancelled_tournaments.html"

# Canonical filename of the dedicated season change-request page. The export
# writer and any reader share one owner for the artifact name instead of each
# reconstructing it.
SEASON_CHANGES_FILENAME = "season_changes.html"
