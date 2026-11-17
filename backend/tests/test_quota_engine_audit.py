"""
Dedicated Verification Suite for Quota Engine, Test Isolation, and Settings Idempotency.
"""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import PlatformQuotaState, SystemSettings, SystemLog
from backend.app.publishers.quota_tracker import (
    get_or_create_quota_state, record_platform_restriction, can_publish,
    get_or_create_settings, get_today_bucket
)

client = TestClient(app)

def test_pause_survives_date_rollover():
    """Verify an active platform pause survives across date bucket transitions."""
    with SessionLocal() as db:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        future_pause = now + timedelta(hours=18)

        # 1. Seed an active pause in a "yesterday" bucket
        old_bucket = "2026-11-01"
        old_state = db.query(PlatformQuotaState).filter_by(platform="youtube", date_bucket=old_bucket).first()
        if not old_state:
            old_state = PlatformQuotaState(
                platform="youtube",
                date_bucket=old_bucket,
                published_today=5,
                quota_units_used=8000,
                channel_limit_reached=True,
                pause_until=future_pause,
                last_restriction_type="channel_limit_reached",
                last_restriction_reason="Channel 24h upload limit"
            )
            db.add(old_state)
        else:
            old_state.channel_limit_reached = True
            old_state.pause_until = future_pause
            old_state.last_restriction_type = "channel_limit_reached"
        db.commit()

        # Delete any state in today's bucket to test rollover generation
        today_bucket = get_today_bucket()
        db.query(PlatformQuotaState).filter_by(platform="youtube", date_bucket=today_bucket).delete()
        db.commit()

        # 2. Query today's state
        today_state = get_or_create_quota_state(db, "youtube")
        assert today_state.date_bucket == today_bucket
        # Counter should reset to 0 for the new day
        assert today_state.published_today == 0
        assert today_state.quota_units_used == 0
        # BUT active pause and restriction state MUST be preserved!
        assert today_state.channel_limit_reached is True
        assert today_state.pause_until == future_pause
        assert today_state.last_restriction_type == "channel_limit_reached"

        # 3. can_publish MUST strictly block publishing
        allowed, reason, next_win = can_publish(db, "youtube")
        assert allowed is False
        assert "paused until" in reason
        assert next_win == future_pause

def test_idempotent_restriction_suppression():
    """Verify duplicate platform restriction events are suppressed and do not spam logs."""
    with SessionLocal() as db:
        # Count existing logs
        initial_log_count = db.query(SystemLog).filter_by(module="publishing_quota").count()

        # First restriction
        win1 = record_platform_restriction(
            db, "instagram", "rate_limit", "Page request limit reached (#32)"
        )
        assert win1 > datetime.now(timezone.utc).replace(tzinfo=None)

        log_count_after_first = db.query(SystemLog).filter_by(module="publishing_quota").count()
        assert log_count_after_first == initial_log_count + 1

        # Second identical restriction within the active pause window
        win2 = record_platform_restriction(
            db, "instagram", "rate_limit", "Page request limit reached (#32)"
        )
        assert win2 == win1

        # Log count MUST NOT increase (duplicate suppressed)
        log_count_after_second = db.query(SystemLog).filter_by(module="publishing_quota").count()
        assert log_count_after_second == log_count_after_first

def test_automatic_recovery_after_pause_expires():
    """Verify restriction automatically clears once pause_until timestamp passes."""
    with SessionLocal() as db:
        state = get_or_create_quota_state(db, "facebook")
        # Artificially set pause_until in the past
        past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
        state.rate_limited = True
        state.pause_until = past_time
        state.last_restriction_type = "rate_limit"
        state.last_restriction_reason = "Expired test rate limit"
        db.commit()

        # Calling get_or_create_quota_state or can_publish should trigger recovery
        refreshed = get_or_create_quota_state(db, "facebook")
        assert refreshed.rate_limited is False
        assert refreshed.pause_until is None
        assert refreshed.last_restriction_type is None

        allowed, reason, _ = can_publish(db, "facebook")
        assert allowed is True
        assert reason is None

def test_settings_update_idempotency():
    """Verify posting unchanged settings does not spam system_logs."""
    with SessionLocal() as db:
        initial_log_count = db.query(SystemLog).filter_by(module="settings").count()

        # Update to specific values
        payload = {
            "daily_generation_target": 15,
            "daily_publishing_target": 15,
            "youtube_daily_limit": 15,
            "instagram_daily_limit": 25,
            "facebook_daily_limit": 25
        }
        res1 = client.post("/api/settings/limits", json=payload)
        assert res1.status_code == 200

        mid_log_count = db.query(SystemLog).filter_by(module="settings").count()

        # Submit identical payload again
        res2 = client.post("/api/settings/limits", json=payload)
        assert res2.status_code == 200

        final_log_count = db.query(SystemLog).filter_by(module="settings").count()
        # Log count should NOT increase when settings are identical
        assert final_log_count == mid_log_count
