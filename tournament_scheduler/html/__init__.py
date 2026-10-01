"""HTML export for the season plan — interactive overview page."""

# Canonical filename of the dedicated cancelled-tournament page. The exporter
# writes it next to the season plan and the export-parity reader reads it back,
# so the two layers share one owner for the artifact name instead of each
# reconstructing it.
CANCELLED_TOURNAMENTS_FILENAME = "cancelled_tournaments.html"
