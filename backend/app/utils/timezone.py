"""
Centralized Timezone Standardization for Gaming Uncut Automation.
Guarantees consistent, timezone-aware datetime handling across all services.

Timezone: Asia/Kolkata (IST, UTC+05:30)
Standard Display Format: 19 Nov 2026, 05:22 AM IST
Compact Display Format:  19/11/2026, 05:22 AM IST
"""

import zoneinfo
from datetime import datetime, timezone
from typing import Optional, Union

# Centralized timezone objects
IST_TIMEZONE = zoneinfo.ZoneInfo("Asia/Kolkata")
UTC_TIMEZONE = timezone.utc


def now_utc() -> datetime:
    """Returns current datetime in UTC (timezone-aware)."""
    return datetime.now(UTC_TIMEZONE)


def now_ist() -> datetime:
    """Returns current datetime in Asia/Kolkata (IST, timezone-aware)."""
    return datetime.now(IST_TIMEZONE)


def to_ist(dt: Union[datetime, str, None]) -> Optional[datetime]:
    """
    Safely converts a UTC datetime (or ISO string) into a timezone-aware Asia/Kolkata datetime.
    Never reinterprets a UTC timestamp as local time without conversion.
    Never adds manual 5:30 offsets; uses zoneinfo exclusively.
    """
    if dt is None:
        return None

    if isinstance(dt, str):
        cleaned = dt.strip()
        if not cleaned or cleaned.lower() in ("null", "none", "pending", "n/a"):
            return None
        # Handle SQLite format "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DD HH:MM:SS.mmmmmm"
        if " " in cleaned and "T" not in cleaned:
            cleaned = cleaned.replace(" ", "T")
        # Ensure UTC timezone indicator if missing
        if not (cleaned.endswith("Z") or "+" in cleaned[-6:] or "-" in cleaned[-6:]):
            cleaned += "+00:00"
        elif cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            return None
        dt = parsed

    if not isinstance(dt, datetime):
        return None

    # If datetime is naive, treat it as UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TIMEZONE)

    return dt.astimezone(IST_TIMEZONE)


def format_datetime_ist(
    dt: Union[datetime, str, None],
    compact: bool = False,
    fallback: str = "N/A"
) -> str:
    """
    Formats a datetime in Indian Standard Time (IST).
    Standard Format: "19 Nov 2026, 05:22 AM IST"
    Compact Format:  "19/11/2026, 05:22 AM IST"
    Always includes the 'IST' suffix.
    """
    ist_dt = to_ist(dt)
    if ist_dt is None:
        return fallback

    if compact:
        return ist_dt.strftime("%d/%m/%Y, %I:%M %p IST")
    return ist_dt.strftime("%d %b %Y, %I:%M %p IST")


def format_date_ist(
    dt: Union[datetime, str, None],
    compact: bool = False,
    fallback: str = "N/A"
) -> str:
    """
    Formats the date portion in Indian Standard Time (IST).
    Standard Format: "19 Nov 2026 IST"
    Compact Format:  "19/11/2026 IST"
    """
    ist_dt = to_ist(dt)
    if ist_dt is None:
        return fallback

    if compact:
        return ist_dt.strftime("%d/%m/%Y IST")
    return ist_dt.strftime("%d %b %Y IST")


def format_time_ist(
    dt: Union[datetime, str, None],
    fallback: str = "N/A"
) -> str:
    """
    Formats the time portion in Indian Standard Time (IST).
    Format: "05:22 AM IST"
    """
    ist_dt = to_ist(dt)
    if ist_dt is None:
        return fallback

    return ist_dt.strftime("%I:%M %p IST")
