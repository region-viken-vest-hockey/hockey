"""Booking domain: calendar availability, booking reconciliation and arena conflicts.

Planner-independent facts for which hosts and arenas are free, which calendar
bookings are real, and how overlapping arena intervals are detected and decided.
Layer: domain/application helpers; CLI and infrastructure must not be imported from here.
"""
