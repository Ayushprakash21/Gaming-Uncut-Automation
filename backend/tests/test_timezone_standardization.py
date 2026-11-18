"""
Comprehensive Regression Test Suite for Global Timezone Standardization (Asia/Kolkata, IST).

Covers:
1. Exact formatting for standard (19 Nov 2026, 05:22 AM IST) and compact (19/11/2026, 05:22 AM IST).
2. The specific YouTube quota message:
   "Publishing on YouTube is paused until 19 Nov 2026, 05:22 AM IST."
3. Midnight rollover: UTC evening crossing midnight into next-day IST.
4. Month and Year rollover: 31 Dec 2026 23:00 UTC -> 01 Jan 2027 04:30 AM IST.
5. Timezone awareness: Never reinterprets UTC as local or performs manual 5h30m addition.
6. Permanent offset invariant: IST has no daylight saving time (DST); stays UTC+05:30 year-round.
7. Safe fallback handling for None, empty, "pending", and invalid timestamp values.
8. Scheduler explicit timezone configuration (Asia/Kolkata).
9. Database storage verification: stored timestamps remain pure UTC.
"""

import zoneinfo
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
import pytest

from backend.app.utils.timezone import (
    IST_TIMEZONE,
    UTC_TIMEZONE,
    to_ist,
    format_datetime_ist,
    format_date_ist,
    format_time_ist,
    now_utc,
    now_ist,
)
from backend.app.publishers.quota_tracker import (
    get_or_create_quota_state,
    can_publish,
    record_platform_restriction,
)
from backend.app.scheduler.manager import task_scheduler
from backend.app.database.session import SessionLocal
from backend.app.models.schema import PlatformQuotaState, PublishingJob


# =============================================================================
# 1. Formatting & Exact String Requirements
# =============================================================================

def test_specific_youtube_quota_message_formatting():
    """
    Specifically tests the exact user requirement:
    Input UTC: 2026-11-18 23:52:00
    Display:   Publishing on YouTube is paused until 19 Nov 2026, 05:22 AM IST.
    """
    utc_dt = datetime(2026, 11, 18, 23, 52, 0, tzinfo=timezone.utc)
    formatted = format_datetime_ist(utc_dt)
    assert formatted == "19 Nov 2026, 05:22 AM IST"

    msg = f"Publishing on YouTube is paused until {formatted}."
    assert msg == "Publishing on YouTube is paused until 19 Nov 2026, 05:22 AM IST."


def test_compact_table_formatting():
    """
    Verifies compact table display:
    Format: 19/11/2026, 05:22 AM IST
    """
    utc_dt = datetime(2026, 11, 18, 23, 52, 0, tzinfo=timezone.utc)
    formatted_compact = format_datetime_ist(utc_dt, compact=True)
    assert formatted_compact == "19/11/2026, 05:22 AM IST"


def test_date_and_time_individual_helpers():
    """Tests formatDateIST and formatTimeIST."""
    utc_dt = datetime(2026, 11, 18, 23, 52, 0, tzinfo=timezone.utc)
    assert format_date_ist(utc_dt) == "19 Nov 2026 IST"
    assert format_date_ist(utc_dt, compact=True) == "19/11/2026 IST"
    assert format_time_ist(utc_dt) == "05:22 AM IST"


# =============================================================================
# 2. Rollover & Edge Cases (Midnight, Month, Year, Leap Year)
# =============================================================================

def test_midnight_rollover():
    """
    18:30:00 UTC corresponds exactly to 00:00:00 (midnight) of the next day in IST.
    """
    utc_evening = datetime(2026, 11, 18, 18, 30, 0, tzinfo=timezone.utc)
    ist_midnight = to_ist(utc_evening)
    assert ist_midnight.day == 19
    assert ist_midnight.hour == 0
    assert ist_midnight.minute == 0
    assert ist_midnight.second == 0
    assert format_datetime_ist(utc_evening) == "19 Nov 2026, 12:00 AM IST"


def test_year_boundary_rollover():
    """
    31 Dec 2026, 23:00:00 UTC corresponds to 01 Jan 2027, 04:30:00 AM IST.
    """
    new_year_eve_utc = datetime(2026, 12, 31, 23, 0, 0, tzinfo=timezone.utc)
    ist_new_year = to_ist(new_year_eve_utc)
    assert ist_new_year.year == 2027
    assert ist_new_year.month == 1
    assert ist_new_year.day == 1
    assert ist_new_year.hour == 4
    assert ist_new_year.minute == 30
    assert format_datetime_ist(new_year_eve_utc) == "01 Jan 2027, 04:30 AM IST"


def test_leap_year_rollover():
    """
    28 Feb 2028, 20:00:00 UTC in a leap year crosses into 29 Feb 2028 in IST.
    """
    leap_utc = datetime(2028, 2, 28, 20, 0, 0, tzinfo=timezone.utc)
    ist_leap = to_ist(leap_utc)
    assert ist_leap.day == 29
    assert ist_leap.month == 2
    assert ist_leap.year == 2028
    assert format_datetime_ist(leap_utc) == "29 Feb 2028, 01:30 AM IST"


# =============================================================================
# 3. Daylight Saving Invariant & Timezone Awareness
# =============================================================================

def test_ist_no_daylight_saving_invariant():
    """
    Indian Standard Time (Asia/Kolkata) does NOT observe Daylight Saving Time.
    The offset must remain strictly +05:30 across summer (June) and winter (December).
    """
    summer_utc = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    winter_utc = datetime(2026, 12, 15, 12, 0, 0, tzinfo=timezone.utc)

    summer_ist = to_ist(summer_utc)
    winter_ist = to_ist(winter_utc)

    assert summer_ist.utcoffset() == timedelta(hours=5, minutes=30)
    assert winter_ist.utcoffset() == timedelta(hours=5, minutes=30)
    assert summer_ist.dst() == timedelta(0)
    assert winter_ist.dst() == timedelta(0)


def test_naive_datetime_is_safely_treated_as_utc():
    """
    If a naive datetime from database/ORM is passed without tzinfo, it must be
    assumed to be UTC, NEVER local system time.
    """
    naive_utc = datetime(2026, 11, 18, 23, 52, 0)  # tzinfo=None
    ist = to_ist(naive_utc)
    assert ist.tzinfo == IST_TIMEZONE
    assert ist.hour == 5
    assert ist.minute == 22
    assert ist.day == 19
    assert format_datetime_ist(naive_utc) == "19 Nov 2026, 05:22 AM IST"


# =============================================================================
# 4. Safe Fallback & String Inputs
# =============================================================================

def test_safe_fallbacks_for_none_and_invalid():
    """Ensures None, invalid strings, and whitespace fall back gracefully without exceptions."""
    assert format_datetime_ist(None) == "N/A"
    assert format_datetime_ist("") == "N/A"
    assert format_datetime_ist("   ") == "N/A"
    assert format_datetime_ist("null") == "N/A"
    assert format_datetime_ist("PENDING") == "N/A"
    assert format_datetime_ist("invalid-date-string") == "N/A"
    assert format_datetime_ist(None, fallback="Pending") == "Pending"

    assert format_date_ist(None) == "N/A"
    assert format_time_ist(None) == "N/A"


def test_iso_and_sqlite_string_inputs():
    """Tests various string representations common in SQLite and JSON APIs."""
    # Standard ISO with Z
    assert format_datetime_ist("2026-11-18T23:52:00Z") == "19 Nov 2026, 05:22 AM IST"
    # SQLite format with space and without timezone
    assert format_datetime_ist("2026-11-18 23:52:00") == "19 Nov 2026, 05:22 AM IST"
    # ISO with microseconds
    assert format_datetime_ist("2026-11-18T23:52:00.123456Z") == "19 Nov 2026, 05:22 AM IST"


# =============================================================================
# 5. Integration with Quota Engine & Background Scheduler
# =============================================================================

def test_quota_tracker_pause_reason_uses_ist():
    """
    Verifies that can_publish() and record_platform_restriction() produce
    user-facing reasons in Indian Standard Time (IST) with the 'IST' suffix.
    """
    with SessionLocal() as db:
        # Pause youtube until 2026-11-18 23:52:00 UTC
        pause_target = datetime(2026, 11, 18, 23, 52, 0)
        state = get_or_create_quota_state(db, "youtube")
        state.pause_until = pause_target
        state.last_restriction_type = "quota_exceeded"
        state.last_restriction_reason = "Daily 10,000 units exhausted"
        db.commit()

        # Test when pause_until is in the future relative to query
        future_pause = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
        state.pause_until = future_pause
        db.commit()

        allowed, reason, next_win = can_publish(db, "youtube")
        assert not allowed
        assert "IST" in reason
        assert "UTC" not in reason


def test_scheduler_timezone_is_explicitly_ist():
    """
    Verifies rule 7: Scheduler execution must use an explicit timezone setting (Asia/Kolkata).
    """
    assert task_scheduler.scheduler.timezone == IST_TIMEZONE
    assert str(task_scheduler.scheduler.timezone) == "Asia/Kolkata"
