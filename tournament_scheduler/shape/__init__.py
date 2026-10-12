"""Tournament shape: hall occupancy, roster-shape feasibility, guest slots and game generation.

``occupancy`` is the canonical hall-occupancy duration contract, ``effective_shape``
the shared feasibility predicate for a tournament's roster shape, ``guest_slots`` the
reserved guest-slot semantics, ``game_generation``/``limited_rounds`` the game
generators for round-robin and limited-round formats, and ``identity`` the durable
tournament identity. Layer: domain helpers; CLI and infrastructure must not be
imported from here.
"""
