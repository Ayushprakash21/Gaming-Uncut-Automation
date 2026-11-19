from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, ForeignKey, Index
)
from sqlalchemy.orm import relationship
from backend.app.database.session import Base

class AppSettingsModel(Base):
    __tablename__ = "app_settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SourceVideo(Base):
    __tablename__ = "source_videos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(Text, nullable=True)
    title = Column(String(500), nullable=True)
    duration = Column(Float, default=0.0)
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    fps = Column(Float, default=0.0)
    format = Column(String(50), nullable=True)
    local_path = Column(Text, nullable=False, unique=True)
    source_type = Column(String(50), default="local")  # youtube, direct_url, local, twitch_vod
    author = Column(String(255), nullable=True)
    license_info = Column(String(255), nullable=True)
    user_game_name = Column(String(150), nullable=True)
    status = Column(String(50), default="ready")  # pending_download, downloading, ready, failed
    created_at = Column(DateTime, default=datetime.utcnow)

    authorizations = relationship("SourceAuthorization", back_populates="source_video", cascade="all, delete-orphan")
    processing_jobs = relationship("ProcessingJob", back_populates="source_video", cascade="all, delete-orphan")
    highlights = relationship("DetectedHighlight", back_populates="source_video", cascade="all, delete-orphan")

class SourceAuthorization(Base):
    __tablename__ = "source_authorizations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_video_id = Column(Integer, ForeignKey("source_videos.id", ondelete="CASCADE"), nullable=True)
    discovered_video_id = Column(Integer, ForeignKey("discovered_videos.id", ondelete="SET NULL"), nullable=True)
    auth_type = Column(String(50), default="user_owned")  # user_owned, creator_authorized, licensed, creative_commons
    authorized_by = Column(String(255), nullable=True)
    source_owner = Column(String(255), nullable=True)
    permitted_scope = Column(String(255), default="shorts_clips_derivative")
    evidence_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    confirmed_at = Column(DateTime, default=datetime.utcnow)

    source_video = relationship("SourceVideo", back_populates="authorizations")
    discovered_video = relationship("DiscoveredVideo", back_populates="authorization")

class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_video_id = Column(Integer, ForeignKey("source_videos.id", ondelete="CASCADE"), nullable=False)
    job_type = Column(String(50), default="full_pipeline")  # analyze_only, render_only, full_pipeline
    status = Column(String(50), default="queued")  # queued, analyzing, rendering, completed, failed, cancelled
    progress_pct = Column(Integer, default=0)
    current_step = Column(String(100), default="Initialized")
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_heartbeat = Column(DateTime, nullable=True)
    worker_pid = Column(Integer, nullable=True)
    cancellation_requested = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    source_video = relationship("SourceVideo", back_populates="processing_jobs")
    clips = relationship("GeneratedClip", back_populates="job", cascade="all, delete-orphan")

class DetectedHighlight(Base):
    __tablename__ = "detected_highlights"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_video_id = Column(Integer, ForeignKey("source_videos.id", ondelete="CASCADE"), nullable=False)
    start_sec = Column(Float, nullable=False)
    end_sec = Column(Float, nullable=False)
    duration_sec = Column(Float, nullable=False)
    score = Column(Float, nullable=False)  # 0 to 100
    event_type = Column(String(100), default="action_climax")  # kill_streak, boss_fight, victory, clutch, funny
    explanation = Column(Text, nullable=True)
    hook_suggestion = Column(String(255), nullable=True)
    caption_suggestion = Column(Text, nullable=True)
    confidence = Column(Float, default=1.0)
    selected_for_edit = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    source_video = relationship("SourceVideo", back_populates="highlights")
    clips = relationship("GeneratedClip", back_populates="highlight")

class GeneratedClip(Base):
    __tablename__ = "generated_clips"
    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=True)
    highlight_id = Column(Integer, ForeignKey("detected_highlights.id", ondelete="SET NULL"), nullable=True)
    output_path = Column(Text, nullable=False, unique=True)
    thumbnail_path = Column(Text, nullable=True)
    duration_sec = Column(Float, default=60.0)
    width = Column(Integer, default=1080)
    height = Column(Integer, default=1920)
    layout_template = Column(String(50), default="blurred_bg")  # dynamic_gameplay, blurred_bg, split_layout
    quality_score = Column(Float, default=85.0)
    validation_status = Column(String(50), default="passed")  # passed, flagged, failed
    created_at = Column(DateTime, default=datetime.utcnow)

    job = relationship("ProcessingJob", back_populates="clips")
    highlight = relationship("DetectedHighlight", back_populates="clips")
    metadata_rel = relationship("ClipMetadata", back_populates="clip", uselist=False, cascade="all, delete-orphan")
    publishing_jobs = relationship("PublishingJob", back_populates="clip", cascade="all, delete-orphan")
    seo_metadata = relationship("ClipSeoMetadata", back_populates="clip", cascade="all, delete-orphan")

class ClipMetadata(Base):
    __tablename__ = "clip_metadata"
    id = Column(Integer, primary_key=True, autoincrement=True)
    clip_id = Column(Integer, ForeignKey("generated_clips.id", ondelete="CASCADE"), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    topic = Column(String(100), nullable=True)  # FPS gameplay, Battle Royale, Horror, etc.
    description = Column(Text, nullable=True)
    hashtags = Column(Text, nullable=True)
    pinned_comment = Column(Text, nullable=True)
    youtube_title = Column(String(100), nullable=True)
    youtube_desc = Column(Text, nullable=True)
    instagram_caption = Column(Text, nullable=True)
    facebook_caption = Column(Text, nullable=True)
    detected_game = Column(String(100), default="Unknown Game")
    game_confidence = Column(Float, default=0.0)
    game_franchise = Column(String(100), nullable=True)
    game_genre = Column(String(100), nullable=True)
    user_game_name = Column(String(150), nullable=True)
    game_mismatch_flag = Column(Boolean, default=False)
    gameplay_summary = Column(Text, nullable=True)
    detected_events_json = Column(Text, nullable=True)
    candidate_titles_json = Column(Text, nullable=True)
    analysis_provider = Column(String(50), default="heuristic")
    analysis_status = Column(String(50), default="complete")
    analysis_fingerprint = Column(String(64), nullable=True)
    
    # Dedicated SEO Intelligence & Quality Fields
    seo_score = Column(Float, default=0.0)
    metadata_confidence = Column(Float, default=0.0)
    metadata_version = Column(Integer, default=1)
    primary_keyword = Column(String(150), nullable=True)
    secondary_keywords_json = Column(Text, nullable=True)
    facebook_title = Column(String(255), nullable=True)
    instagram_alt_text = Column(Text, nullable=True)
    youtube_tags_json = Column(Text, nullable=True)
    thumbnail_text = Column(String(150), nullable=True)
    original_metadata_json = Column(Text, nullable=True)
    duplicate_detection_json = Column(Text, nullable=True)
    is_manually_edited = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    clip = relationship("GeneratedClip", back_populates="metadata_rel")

class PublishingAccount(Base):
    __tablename__ = "publishing_accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(50), nullable=False)  # youtube, instagram, facebook
    account_id = Column(String(100), nullable=True)
    account_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    token_expires_at = Column(DateTime, nullable=True)
    last_verified_at = Column(DateTime, nullable=True)

class PublishingJob(Base):
    __tablename__ = "publishing_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    clip_id = Column(Integer, ForeignKey("generated_clips.id", ondelete="CASCADE"), nullable=False)
    scheduled_time = Column(DateTime, nullable=False)
    status = Column(String(50), default="QUEUED")  # QUEUED, CLAIMED, UPLOADING, PROCESSING, PUBLISHED, FAILED, UNKNOWN, PAUSED
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=5)
    approval_status = Column(String(50), default="approved")  # approved, auto_approved, pending_review, rejected
    worker_id = Column(String(100), nullable=True)  # Worker lease identifier for atomic claim
    locked_at = Column(DateTime, nullable=True)  # Timestamp lease was acquired
    lock_expires_at = Column(DateTime, nullable=True)  # Timestamp lease expires
    allow_republish = Column(Boolean, default=False)  # User explicit republish override
    created_at = Column(DateTime, default=datetime.utcnow)

    clip = relationship("GeneratedClip", back_populates="publishing_jobs")
    uploads = relationship("PlatformUpload", back_populates="publishing_job", cascade="all, delete-orphan")

class PlatformUpload(Base):
    __tablename__ = "platform_uploads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    publishing_job_id = Column(Integer, ForeignKey("publishing_jobs.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)  # youtube, instagram, facebook
    account_id = Column(String(100), nullable=True)  # Page ID, IG Account ID, YouTube Channel ID
    destination = Column(String(50), default="reels")  # reels, shorts
    idempotency_key = Column(String(255), nullable=True, index=True)  # clip_id:platform:account_id:destination
    external_post_id = Column(String(255), nullable=True)
    external_url = Column(Text, nullable=True)
    status = Column(String(50), default="QUEUED")  # QUEUED, CLAIMED, UPLOADING, PROCESSING, PUBLISHED, FAILED, UNKNOWN, PAUSED
    error_message = Column(Text, nullable=True)
    error_code = Column(String(50), nullable=True)
    attempts = Column(Integer, default=0)
    status_checks = Column(Integer, default=0)
    publishing_attempts = Column(Integer, default=0)
    reconciliation_metadata = Column(Text, nullable=True)
    last_attempt_at = Column(DateTime, nullable=True)
    uploaded_at = Column(DateTime, nullable=True)

    publishing_job = relationship("PublishingJob", back_populates="uploads")
    analytics = relationship("PublishingAnalytics", back_populates="upload", cascade="all, delete-orphan")

class PublishingIdempotencyRecord(Base):
    """
    Enforces persistent database-level idempotency for active and completed social media publications.
    Guarantees the exact same clip is never published multiple times to the same platform and account.
    """
    __tablename__ = "publishing_idempotency_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String(255), nullable=False, unique=True, index=True)
    clip_id = Column(Integer, ForeignKey("generated_clips.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(50), nullable=False, index=True)
    account_id = Column(String(100), nullable=False)
    destination = Column(String(50), nullable=False, default="reels")
    publishing_job_id = Column(Integer, ForeignKey("publishing_jobs.id", ondelete="SET NULL"), nullable=True)
    platform_upload_id = Column(Integer, ForeignKey("platform_uploads.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(50), nullable=False, default="CLAIMED")  # CLAIMED, UPLOADING, PROCESSING, PUBLISHED, FAILED, UNKNOWN, PAUSED
    external_post_id = Column(String(255), nullable=True)
    external_url = Column(Text, nullable=True)
    worker_id = Column(String(100), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    lock_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class PublishingAnalytics(Base):
    __tablename__ = "publishing_analytics"
    id = Column(Integer, primary_key=True, autoincrement=True)
    platform_upload_id = Column(Integer, ForeignKey("platform_uploads.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)
    external_post_id = Column(String(255), nullable=False)
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    shares = Column(Integer, default=0)
    engagement_rate = Column(Float, default=0.0)
    last_synced_at = Column(DateTime, default=datetime.utcnow)

    upload = relationship("PlatformUpload", back_populates="analytics")

class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), default="Daily Default")
    target_daily_clips = Column(Integer, default=15)
    time_slots_json = Column(Text, default="[]")  # List of ["09:00", "11:30", "14:00", ...]
    is_active = Column(Boolean, default=True)

class SystemSettings(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    daily_generation_target = Column(Integer, default=15)
    daily_publishing_target = Column(Integer, default=15)
    youtube_daily_limit = Column(Integer, default=15)
    instagram_daily_limit = Column(Integer, default=25)
    facebook_daily_limit = Column(Integer, default=25)
    auto_reschedule_on_quota = Column(Boolean, default=True)
    updated_at = Column(DateTime, default=datetime.utcnow)

class PlatformQuotaState(Base):
    __tablename__ = "platform_quota_states"
    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(50), nullable=False)  # youtube, instagram, facebook
    date_bucket = Column(String(10), nullable=False)  # YYYY-MM-DD UTC
    published_today = Column(Integer, default=0)
    quota_units_used = Column(Integer, default=0)  # 1600 units per YouTube upload
    api_quota_exceeded = Column(Boolean, default=False)
    channel_limit_reached = Column(Boolean, default=False)
    rate_limited = Column(Boolean, default=False)
    pause_until = Column(DateTime, nullable=True)
    last_restriction_type = Column(String(100), nullable=True)  # "api_quota_exceeded", "channel_limit_reached", "rate_limited", "user_limit_reached"
    last_restriction_reason = Column(Text, nullable=True)
    last_updated_at = Column(DateTime, default=datetime.utcnow)

class CredentialsMetadata(Base):
    __tablename__ = "credentials_metadata"
    id = Column(Integer, primary_key=True, autoincrement=True)
    service_name = Column(String(100), nullable=False)
    key_name = Column(String(100), nullable=False)
    is_set = Column(Boolean, default=False)
    last_updated_at = Column(DateTime, default=datetime.utcnow)

class SystemLog(Base):
    __tablename__ = "system_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    module = Column(String(50), nullable=False)
    level = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ApplicationEvent(Base):
    __tablename__ = "application_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(100), nullable=False)
    details_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DiscoverySearch(Base):
    __tablename__ = "discovery_searches"
    id = Column(Integer, primary_key=True, autoincrement=True)
    query = Column(String(255), nullable=False)
    game_title = Column(String(100), nullable=True)
    search_type = Column(String(50), default="manual")  # manual, automatic
    status = Column(String(50), default="completed")  # completed, failed, in_progress
    results_count = Column(Integer, default=0)
    quota_units_used = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    discovered_videos = relationship("DiscoveredVideo", back_populates="discovery_search")

class DiscoverySource(Base):
    __tablename__ = "discovery_sources"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    game_title = Column(String(100), nullable=False)
    keywords_json = Column(Text, default="[]")  # JSON array of keywords
    is_active = Column(Boolean, default=True)
    min_duration_sec = Column(Float, default=60.0)
    max_duration_sec = Column(Float, default=1800.0)
    min_views = Column(Integer, default=0)
    max_results_per_scan = Column(Integer, default=10)
    priority = Column(Integer, default=1)
    last_scanned_at = Column(DateTime, nullable=True)
    next_scan_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class DiscoveryChannel(Base):
    __tablename__ = "discovery_channels"
    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(100), unique=True, nullable=False, index=True)
    channel_title = Column(String(255), nullable=False)
    custom_url = Column(String(255), nullable=True)
    is_approved = Column(Boolean, default=True)
    auto_authorize_sources = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class DiscoverySchedule(Base):
    __tablename__ = "discovery_schedules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), default="Default Discovery Schedule")
    interval_minutes = Column(Integer, default=180)  # e.g. 180 min (3 hours)
    is_active = Column(Boolean, default=True)
    max_discoveries_per_scan = Column(Integer, default=10)
    max_videos_processed_per_day = Column(Integer, default=5)
    max_clips_generated_per_day = Column(Integer, default=15)
    ai_budget_daily = Column(Integer, default=100)
    auto_approve_clips = Column(Boolean, default=False)
    auto_publish = Column(Boolean, default=False)  # SAFETY: MUST DEFAULT TO FALSE
    last_scan_at = Column(DateTime, nullable=True)
    next_scan_at = Column(DateTime, nullable=True)
    last_scan_status = Column(String(50), default="idle")  # idle, running, success, failed, paused_quota
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    jobs = relationship("DiscoveryJob", back_populates="schedule", cascade="all, delete-orphan")

class DiscoveryJob(Base):
    __tablename__ = "discovery_jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, ForeignKey("discovery_schedules.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(50), default="pending")  # pending, running, completed, failed, paused_quota
    videos_found = Column(Integer, default=0)
    videos_imported = Column(Integer, default=0)
    quota_used = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    schedule = relationship("DiscoverySchedule", back_populates="jobs")
    discovered_videos = relationship("DiscoveredVideo", back_populates="discovery_job")

class DiscoveredVideo(Base):
    __tablename__ = "discovered_videos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    youtube_video_id = Column(String(50), unique=True, index=True, nullable=False)
    title = Column(String(500), nullable=False)
    channel_id = Column(String(100), nullable=True)
    channel_title = Column(String(255), nullable=True)
    published_at = Column(DateTime, nullable=True)
    duration_sec = Column(Float, default=0.0)
    view_count = Column(Integer, default=0)
    thumbnail_url = Column(Text, nullable=True)
    youtube_url = Column(Text, nullable=False)
    game_title = Column(String(100), default="Unknown Game")
    discovery_mode = Column(String(50), default="manual")  # manual, automatic
    discovery_search_id = Column(Integer, ForeignKey("discovery_searches.id", ondelete="SET NULL"), nullable=True)
    discovery_job_id = Column(Integer, ForeignKey("discovery_jobs.id", ondelete="SET NULL"), nullable=True)
    status = Column(String(50), default="discovered", index=True)
    # Unified statuses:
    # discovered, authorization_required, authorized, queued, analyzing,
    # highlights_detected, rendering, review_required, ready_to_publish, published, failed, skipped
    source_video_id = Column(Integer, ForeignKey("source_videos.id", ondelete="SET NULL"), nullable=True)
    processing_job_id = Column(Integer, ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True)
    clips_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    authorization = relationship("SourceAuthorization", back_populates="discovered_video", uselist=False, cascade="all, delete-orphan")
    source_video = relationship("SourceVideo")
    processing_job = relationship("ProcessingJob")
    discovery_search = relationship("DiscoverySearch", back_populates="discovered_videos")
    discovery_job = relationship("DiscoveryJob", back_populates="discovered_videos")

class ClipSeoMetadata(Base):
    __tablename__ = "clip_seo_metadata"
    id = Column(Integer, primary_key=True, autoincrement=True)
    clip_id = Column(Integer, ForeignKey("generated_clips.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)  # youtube, instagram, facebook
    
    # Platform-specific titles & hooks
    recommended_title = Column(String(255), nullable=True)
    candidate_titles_json = Column(Text, nullable=True)  # JSON list of title or caption alternatives
    recommended_caption = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    
    # SEO Keyword Targeting
    primary_keyword = Column(String(100), nullable=True)
    secondary_keywords_json = Column(Text, nullable=True)  # JSON list
    tags_json = Column(Text, nullable=True)  # JSON list of tags
    hashtags_json = Column(Text, nullable=True)  # JSON list of hashtags
    search_phrases_json = Column(Text, nullable=True)  # JSON list of game search queries
    gameplay_event_keywords_json = Column(Text, nullable=True)  # JSON list of in-game event keywords
    
    # Visual & Engagement Optimization
    thumbnail_hook_text = Column(String(150), nullable=True)
    opening_hook = Column(Text, nullable=True)
    search_intent = Column(String(50), nullable=True)  # entertainment, clutch_skill, how_to, cinematic
    cta = Column(Text, nullable=True)
    onscreen_text = Column(Text, nullable=True)
    topic_suggestions_json = Column(Text, nullable=True)
    
    # SEO Quality Scoring (0-100)
    seo_score = Column(Float, default=0.0)
    score_breakdown_json = Column(Text, nullable=True)  # JSON object of 7 dimensions
    warnings_json = Column(Text, nullable=True)  # JSON list of warnings
    recommendations_json = Column(Text, nullable=True)  # JSON list of suggested improvements
    
    # Operational Status
    approval_status = Column(String(50), default="pending")  # pending, approved, rejected
    ai_provider = Column(String(50), default="local")  # ollama, gemini, heuristic
    ai_model = Column(String(100), nullable=True)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    clip = relationship("GeneratedClip", back_populates="seo_metadata")

class KeywordResearchRecord(Base):
    __tablename__ = "keyword_research_records"
    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(200), nullable=False, index=True)
    game_name = Column(String(150), nullable=True, index=True)
    platform = Column(String(50), default="youtube")
    source = Column(String(50), nullable=False)  # youtube_api, youtube_autocomplete, trends, historical_db, ai_estimate
    is_measured = Column(Boolean, default=False)  # True only if measured from authorized API
    search_volume_score = Column(Float, nullable=True)  # Relative scale 0-100 or metric
    competition_score = Column(Float, nullable=True)
    trend_direction = Column(String(20), default="stable")  # rising, stable, declining
    related_queries_json = Column(Text, nullable=True)
    retrieved_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

class SeoGrowthInsight(Base):
    __tablename__ = "seo_growth_insights"
    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(50), nullable=False)  # topic, hook, title_pattern, game, timing
    dimension_name = Column(String(150), nullable=False)
    platform = Column(String(50), default="all")
    performance_score = Column(Float, default=0.0)
    sample_size = Column(Integer, default=0)
    insight_summary = Column(Text, nullable=False)
    recommendation = Column(Text, nullable=True)
    is_statistically_significant = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
