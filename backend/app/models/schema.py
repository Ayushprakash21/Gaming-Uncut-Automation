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
    status = Column(String(50), default="ready")  # pending_download, downloading, ready, failed
    created_at = Column(DateTime, default=datetime.utcnow)

    authorizations = relationship("SourceAuthorization", back_populates="source_video", cascade="all, delete-orphan")
    processing_jobs = relationship("ProcessingJob", back_populates="source_video", cascade="all, delete-orphan")
    highlights = relationship("DetectedHighlight", back_populates="source_video", cascade="all, delete-orphan")

class SourceAuthorization(Base):
    __tablename__ = "source_authorizations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_video_id = Column(Integer, ForeignKey("source_videos.id", ondelete="CASCADE"), nullable=False)
    auth_type = Column(String(50), default="user_owned")  # user_owned, creator_authorized, licensed, creative_commons
    authorized_by = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    confirmed_at = Column(DateTime, default=datetime.utcnow)

    source_video = relationship("SourceVideo", back_populates="authorizations")

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
    created_at = Column(DateTime, default=datetime.utcnow)

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
    status = Column(String(50), default="pending")  # pending, publishing, published, failed, paused, cancelled
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=5)
    approval_status = Column(String(50), default="approved")  # approved, auto_approved, pending_review, rejected
    created_at = Column(DateTime, default=datetime.utcnow)

    clip = relationship("GeneratedClip", back_populates="publishing_jobs")
    uploads = relationship("PlatformUpload", back_populates="publishing_job", cascade="all, delete-orphan")

class PlatformUpload(Base):
    __tablename__ = "platform_uploads"
    id = Column(Integer, primary_key=True, autoincrement=True)
    publishing_job_id = Column(Integer, ForeignKey("publishing_jobs.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)  # youtube, instagram, facebook
    external_post_id = Column(String(255), nullable=True)
    external_url = Column(Text, nullable=True)
    status = Column(String(50), default="pending")  # pending, uploading, processing, published, failed
    error_message = Column(Text, nullable=True)
    attempts = Column(Integer, default=0)
    uploaded_at = Column(DateTime, nullable=True)

    publishing_job = relationship("PublishingJob", back_populates="uploads")
    analytics = relationship("PublishingAnalytics", back_populates="upload", cascade="all, delete-orphan")

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
