"""
Quota, Rate-Limit, and Window Management Engine for Gaming Uncut Automation.
Tracks:
1. User-configured daily generation & publishing targets (default: 15/day).
2. YouTube Data API v3 quota units (1,600 units per video.insert).
3. YouTube channel-level daily upload restrictions (rolling 24h).
4. Instagram & Facebook Graph API rate limits and publishing windows.
5. Automatic rollover, persistent pause states across date buckets, and duplicate-event suppression.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional, Dict, Any
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import SystemSettings, PlatformQuotaState, PlatformUpload, PublishingJob, SystemLog
from backend.app.utils.timezone import format_datetime_ist

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
    """
    Retrieves or initializes today's quota state for a given platform.
    CRITICAL ARCHITECTURE RULE:
    If a previous date bucket holds an active restriction (pause_until in the future),
    that restriction MUST be carried forward into the new bucket and never silently cleared
    simply because of a midnight date rollover.
    """
    bucket = get_today_bucket()
    state = db.query(PlatformQuotaState).filter_by(
        platform=platform, date_bucket=bucket
    ).first()

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if not state:
        # Check if there is an active pause from a recent previous bucket
        prev_state = db.query(PlatformQuotaState).filter(
            PlatformQuotaState.platform == platform,
            PlatformQuotaState.pause_until.isnot(None),
            PlatformQuotaState.pause_until > now
        ).order_by(PlatformQuotaState.id.desc()).first()

        active_pause = prev_state.pause_until if prev_state else None
        active_api_quota = prev_state.api_quota_exceeded if prev_state else False
        active_channel_limit = prev_state.channel_limit_reached if prev_state else False
        active_rate_limit = prev_state.rate_limited if prev_state else False
        active_restr_type = prev_state.last_restriction_type if prev_state else None
        active_restr_reason = prev_state.last_restriction_reason if prev_state else None

        state = PlatformQuotaState(
            platform=platform,
            date_bucket=bucket,
            published_today=0,
            quota_units_used=0,
            api_quota_exceeded=active_api_quota,
            channel_limit_reached=active_channel_limit,
            rate_limited=active_rate_limit,
            pause_until=active_pause,
            last_restriction_type=active_restr_type,
            last_restriction_reason=active_restr_reason
        )
        db.add(state)
        db.commit()
        db.refresh(state)

    # Check if a past pause_until has expired
    if state.pause_until and state.pause_until <= now:
        state.api_quota_exceeded = False
        state.channel_limit_reached = False
        state.rate_limited = False
        state.pause_until = None
        state.last_restriction_type = None
        state.last_restriction_reason = None
        state.last_updated_at = now
        db.commit()

    return state

def calculate_next_window(
    platform: str,
    restriction_type: str,
    retry_after_seconds: Optional[int] = None
) -> datetime:
    """
    Calculates the exact timestamp when the next publishing window opens:
    1. YouTube API quota: Resets at midnight Pacific Time (00:00 PST = 08:00 UTC).
    2. YouTube channel upload limits: Rolling 24 hours from occurrence.
    3. Meta rate limits: Specified retry_after or rolling 24 hours.
    4. Transient rate limits: Exponential or short backoff (15-60 min).
    5. User-configured daily target: Midnight UTC next day.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if retry_after_seconds and retry_after_seconds > 0:
        return now + timedelta(seconds=retry_after_seconds)

    if restriction_type in ["api_quota_exceeded", "quota_exceeded"]:
        # Google API quota resets daily at 00:00 Pacific (08:00 UTC)
        next_8am = now.replace(hour=8, minute=5, second=0, microsecond=0)
        if next_8am <= now:
            next_8am += timedelta(days=1)
        return next_8am
    elif restriction_type in ["channel_limit_reached", "channel_limit"]:
        # YouTube channel upload limit is a rolling 24-hour restriction
        return now + timedelta(hours=24)
    elif restriction_type == "user_limit_reached":
        # User limit rolls over at midnight UTC
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        return next_midnight
    elif restriction_type == "transient_backoff":
        return now + timedelta(minutes=15)
    else:
        # Default Meta or generic rate limit rollover (24 hours)
        return now + timedelta(hours=24)

def is_platform_authorized(platform: str) -> Tuple[bool, Optional[str]]:
    """
    Evaluates whether required account authorization exists for the platform.
    Never permits uploads to disconnected platforms.
    """
    from backend.app.security.vault import vault

    if platform == "youtube":
        client_cfg = vault.get_json("google", "client_config")
        tokens = vault.get_json("google", "oauth_tokens")
        channel = vault.get_json("google", "verified_channel")
        if not client_cfg:
            return False, "Google OAuth client configuration missing. Import client secrets in Settings."
        if not tokens:
            return False, "YouTube user account not authorized. Connect YouTube account in Settings."
        if not channel:
            return False, "YouTube channel identity not verified. Connect YouTube account in Settings."
        return True, None
    elif platform == "instagram":
        try:
            from backend.app.publishers.meta_token_manager import meta_token_manager
            health = meta_token_manager.inspect_health()
            if health.get("configured") and (health.get("is_expired") or not health.get("is_valid", True)):
                return False, health.get("last_auth_error") or "Meta Access Token has expired (Code 190, Subcode 463)."
        except Exception:
            pass
        token = vault.get_secret("meta", "system_user_token") or vault.get_secret("meta", "page_access_token")
        ig_id = vault.get_secret("meta", "instagram_account_id")
        if not token or not ig_id:
            return False, "Instagram Business account not connected. Configure Meta token & IG ID in Settings."
        return True, None
    elif platform == "facebook":
        try:
            from backend.app.publishers.meta_token_manager import meta_token_manager
            health = meta_token_manager.inspect_health()
            if health.get("configured") and (health.get("is_expired") or not health.get("is_valid", True)):
                return False, health.get("last_auth_error") or "Meta Access Token has expired (Code 190, Subcode 463)."
        except Exception:
            pass
        token = vault.get_secret("meta", "system_user_token") or vault.get_secret("meta", "page_access_token")
        page_id = vault.get_secret("meta", "page_id")
        if not token:
            return False, "Facebook Page not authorized. Configure Meta access token in Settings."
        if not page_id:
            return False, "Facebook Page ID not configured. Configure Facebook Page ID in Settings."
        return True, None
    return False, f"Unknown platform: '{platform}'"

def can_publish(db: Session, platform: str, check_auth: bool = True) -> Tuple[bool, Optional[str], Optional[datetime]]:
    """
    Evaluates whether publishing to the platform is currently permitted.
    Returns: (is_allowed: bool, reason: Optional[str], next_window: Optional[datetime])
    Ensures NO API calls are executed while a platform is paused, unauthenticated, or restricted.
    """
    cfg = get_or_create_settings(db)
    state = get_or_create_quota_state(db, platform)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Check if platform is actively paused by restriction or quota
    if state.pause_until and state.pause_until > now:
        restr = state.last_restriction_type or "restriction"
        reason = (
            f"Publishing on {platform} is paused until {format_datetime_ist(state.pause_until)} "
            f"due to {restr} ({state.last_restriction_reason or 'Active platform restriction'})."
        )
        return False, reason, state.pause_until

    # 2. Check platform authorization gate (Strict Queue Safety)
    if check_auth:
        auth_ok, auth_reason = is_platform_authorized(platform)
        if not auth_ok:
            return False, f"Authorization required: {auth_reason}", None

    # 3. Check user-configured platform limits
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
    reason: str,
    retry_after_seconds: Optional[int] = None
) -> datetime:
    """
    Records an official platform restriction, pauses publishing for this platform,
    calculates the next permitted window, and logs an audit record.
    
    IDEMPOTENCY & DUPLICATE SUPPRESSION:
    If the platform is already paused with the same restriction type and an active window,
    duplicate log entries and repeated state writes are suppressed.
    """
    state = get_or_create_quota_state(db, platform)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Duplicate-event suppression: if already paused for the same restriction in the future
    if state.pause_until and state.pause_until > now and state.last_restriction_type == restriction_type:
        logger.info(
            f"Duplicate restriction suppressed for {platform} [{restriction_type}]. "
            f"Already paused until {format_datetime_ist(state.pause_until)}."
        )
        return state.pause_until

    next_window = calculate_next_window(platform, restriction_type, retry_after_seconds=retry_after_seconds)

    if restriction_type in ["api_quota_exceeded", "quota_exceeded"]:
        state.api_quota_exceeded = True
    elif restriction_type in ["channel_limit_reached", "channel_limit"]:
        state.channel_limit_reached = True
    else:
        state.rate_limited = True

    state.pause_until = next_window
    state.last_restriction_type = restriction_type
    state.last_restriction_reason = reason
    state.last_updated_at = now

    log = SystemLog(
        module="publishing_quota",
        level="WARNING",
        message=f"{platform.upper()} restriction detected [{restriction_type}]: {reason}. Publishing paused until {format_datetime_ist(next_window)}."
    )
    db.add(log)
    db.commit()
    logger.warning(f"Platform restriction registered for {platform}: {reason}. Next window: {next_window}")
    return next_window
