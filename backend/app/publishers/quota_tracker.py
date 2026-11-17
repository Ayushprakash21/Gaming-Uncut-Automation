"""
Quota, Rate-Limit, and Window Management Engine for Gaming Uncut Automation.
Tracks:
1. User-configured daily generation & publishing targets (default: 15/day).
2. YouTube Data API v3 quota units (1,600 units per video.insert).
3. YouTube channel-level daily upload restrictions.
4. Instagram & Facebook Graph API rate limits and publishing windows.
5. Automatic rollover and rescheduling for the next permitted window.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional, Dict, Any
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import SystemSettings, PlatformQuotaState, PlatformUpload, PublishingJob, SystemLog

logger = logging.getLogger("gaming_uncut.quota_tracker")

class YouTubeQuotaExceededError(Exception):
    """Google Cloud Project Data API v3 quota exhausted (10,000 unit default bucket)."""
    pass

class YouTubeChannelUploadLimitError(Exception):
    """Channel-specific upload limit reached for the day (unverified or daily tier cap)."""
    pass

class InstagramRateLimitError(Exception):
    """Instagram Graph API publishing rate limit exceeded."""
    pass

class FacebookRateLimitError(Exception):
    """Facebook Page Video Reels rate limit exceeded."""
    pass

def get_today_bucket() -> str:
    """Returns today's date bucket in UTC (YYYY-MM-DD)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def get_or_create_settings(db: Session) -> SystemSettings:
    """Retrieves or initializes the system settings singleton."""
    cfg = db.query(SystemSettings).first()
    if not cfg:
        cfg = SystemSettings(
            daily_generation_target=settings.DAILY_TARGET_CLIPS,
            daily_publishing_target=settings.DAILY_TARGET_CLIPS,
            youtube_daily_limit=settings.YOUTUBE_DEFAULT_DAILY_LIMIT,
            instagram_daily_limit=settings.INSTAGRAM_DEFAULT_DAILY_LIMIT,
            facebook_daily_limit=settings.FACEBOOK_DEFAULT_DAILY_LIMIT,
            auto_reschedule_on_quota=True
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg

def get_or_create_quota_state(db: Session, platform: str) -> PlatformQuotaState:
    """Retrieves or initializes today's quota state for a given platform."""
    bucket = get_today_bucket()
    state = db.query(PlatformQuotaState).filter_by(
        platform=platform, date_bucket=bucket
    ).first()

    if not state:
        state = PlatformQuotaState(
            platform=platform,
            date_bucket=bucket,
            published_today=0,
            quota_units_used=0,
            api_quota_exceeded=False,
            channel_limit_reached=False,
            rate_limited=False,
            pause_until=None
        )
        db.add(state)
        db.commit()
        db.refresh(state)

    # Check if a past pause_until has expired
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if state.pause_until and state.pause_until <= now:
        state.api_quota_exceeded = False
        state.channel_limit_reached = False
        state.rate_limited = False
        state.pause_until = None
        state.last_restriction_type = None
        state.last_restriction_reason = None
        db.commit()

    return state

def calculate_next_window(platform: str, restriction_type: str) -> datetime:
    """
    Calculates the exact timestamp when the next publishing window opens:
    - YouTube API quota: Resets at midnight Pacific Time (00:00 PST = 08:00 UTC next day).
    - Channel upload limits: Rolling 24 hours.
    - Meta rate limits: Rolling 24 hours or next day 09:00 UTC.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if restriction_type == "api_quota_exceeded":
        # Google API quota resets daily at 00:00 Pacific (08:00 UTC)
        tomorrow_8am = now.replace(hour=8, minute=5, second=0, microsecond=0)
        if tomorrow_8am <= now:
            tomorrow_8am += timedelta(days=1)
        return tomorrow_8am
    elif restriction_type == "channel_limit_reached":
        # YouTube channel upload limit resets 24h from restriction
        return now + timedelta(hours=24)
    elif restriction_type == "user_limit_reached":
        # User limit rolls over at midnight UTC
        next_day = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        return next_day
    else:
        # Default Meta or generic rate limit rollover (24 hours)
        return now + timedelta(hours=24)

def can_publish(db: Session, platform: str) -> Tuple[bool, Optional[str], Optional[datetime]]:
    """
    Evaluates whether publishing to the platform is currently permitted.
    Returns: (is_allowed: bool, reason: Optional[str], next_window: Optional[datetime])
    """
    cfg = get_or_create_settings(db)
    state = get_or_create_quota_state(db, platform)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Check if platform is actively paused
    if state.pause_until and state.pause_until > now:
        reason = f"Publishing on {platform} paused until {state.pause_until.strftime('%Y-%m-%d %H:%M UTC')} ({state.last_restriction_reason})"
        return False, reason, state.pause_until

    # 2. Check user-configured platform limits
    limit_map = {
        "youtube": cfg.youtube_daily_limit,
        "instagram": cfg.instagram_daily_limit,
        "facebook": cfg.facebook_daily_limit
    }
    user_limit = limit_map.get(platform, 15)

    if state.published_today >= user_limit:
        next_win = calculate_next_window(platform, "user_limit_reached")
        reason = f"User-configured daily limit for {platform} reached ({state.published_today}/{user_limit})."
        return False, reason, next_win

    # 3. Check overall daily publishing target
    total_published_today = db.query(PlatformUpload).filter(
        PlatformUpload.status == "published",
        PlatformUpload.uploaded_at >= now.replace(hour=0, minute=0, second=0, microsecond=0)
    ).count()

    if total_published_today >= cfg.daily_publishing_target:
        next_win = calculate_next_window(platform, "user_limit_reached")
        reason = f"Overall daily publishing target reached ({total_published_today}/{cfg.daily_publishing_target})."
        return False, reason, next_win

    return True, None, None

def record_upload_success(db: Session, platform: str, quota_units: int = 0):
    """Updates counts and consumed quota units upon successful publication."""
    state = get_or_create_quota_state(db, platform)
    state.published_today += 1
    state.quota_units_used += quota_units
    state.last_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

def record_platform_restriction(
    db: Session,
    platform: str,
    restriction_type: str,
    reason: str
) -> datetime:
    """
    Records an official platform restriction, pauses publishing for this platform,
    calculates the next permitted window, and logs an audit record.
    """
    state = get_or_create_quota_state(db, platform)
    next_window = calculate_next_window(platform, restriction_type)

    if restriction_type in ["api_quota_exceeded", "quota_exceeded"]:
        state.api_quota_exceeded = True
    elif restriction_type in ["channel_limit_reached", "channel_limit"]:
        state.channel_limit_reached = True
    else:
        state.rate_limited = True

    state.pause_until = next_window
    state.last_restriction_type = restriction_type
    state.last_restriction_reason = reason
    state.last_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    log = SystemLog(
        module="publishing_quota",
        level="WARNING",
        message=f"{platform.upper()} restriction detected [{restriction_type}]: {reason}. Publishing paused until {next_window.strftime('%Y-%m-%d %H:%M UTC')}."
    )
    db.add(log)
    db.commit()
    logger.warning(f"Platform restriction registered for {platform}: {reason}. Next window: {next_window}")
    return next_window
