from datetime import date, timedelta

from app.models import DriveLimit


def trip_days(start: date, end: date) -> int:
    return (end - start).days + 1


def max_drive_minutes(
    start: date, end: date, limit: DriveLimit, day_trip_max: int, weekend_max: int
) -> int | None:
    """Max driving minutes from home, or None for unlimited."""
    if limit == "unlimited":
        return None
    if isinstance(limit, int):
        return limit
    days = trip_days(start, end)
    if days <= 1:
        return day_trip_max
    if days <= 3:
        return weekend_max
    return None


def stay_window(start: date, end: date) -> tuple[date, date]:
    """Check-in/check-out; a same-day trip still books one night."""
    return start, max(end, start + timedelta(days=1))
