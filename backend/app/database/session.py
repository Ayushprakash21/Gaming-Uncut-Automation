from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from backend.app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30.0}
)

# Enable WAL mode and foreign key constraints on SQLite connections
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()

_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def set_session_factory(factory):
    global _session_factory
    _session_factory = factory

class _SessionLocalProxy:
    def __call__(self, *args, **kwargs):
        return _session_factory(*args, **kwargs)

SessionLocal = _SessionLocalProxy()
Base = declarative_base()

def ensure_schema_compatibility(db_engine):
    """Ensures existing SQLite databases contain newly added columns without data loss."""
    from sqlalchemy import inspect, text
    try:
        inspector = inspect(db_engine)
        if "processing_jobs" in inspector.get_table_names():
            columns = [c["name"] for c in inspector.get_columns("processing_jobs")]
            with db_engine.connect() as conn:
                if "last_heartbeat" not in columns:
                    conn.execute(text("ALTER TABLE processing_jobs ADD COLUMN last_heartbeat DATETIME"))
                if "worker_pid" not in columns:
                    conn.execute(text("ALTER TABLE processing_jobs ADD COLUMN worker_pid INTEGER"))
                if "cancellation_requested" not in columns:
                    conn.execute(text("ALTER TABLE processing_jobs ADD COLUMN cancellation_requested BOOLEAN DEFAULT 0"))
                conn.commit()
        if "platform_uploads" in inspector.get_table_names():
            p_columns = [c["name"] for c in inspector.get_columns("platform_uploads")]
            with db_engine.connect() as conn:
                if "error_code" not in p_columns:
                    conn.execute(text("ALTER TABLE platform_uploads ADD COLUMN error_code VARCHAR(50)"))
                if "account_id" not in p_columns:
                    conn.execute(text("ALTER TABLE platform_uploads ADD COLUMN account_id VARCHAR(100)"))
                if "destination" not in p_columns:
                    conn.execute(text("ALTER TABLE platform_uploads ADD COLUMN destination VARCHAR(50) DEFAULT 'reels'"))
                if "idempotency_key" not in p_columns:
                    conn.execute(text("ALTER TABLE platform_uploads ADD COLUMN idempotency_key VARCHAR(255)"))
                if "last_attempt_at" not in p_columns:
                    conn.execute(text("ALTER TABLE platform_uploads ADD COLUMN last_attempt_at DATETIME"))
                if "status_checks" not in p_columns:
                    conn.execute(text("ALTER TABLE platform_uploads ADD COLUMN status_checks INTEGER DEFAULT 0"))
                if "publishing_attempts" not in p_columns:
                    conn.execute(text("ALTER TABLE platform_uploads ADD COLUMN publishing_attempts INTEGER DEFAULT 0"))
                if "reconciliation_metadata" not in p_columns:
                    conn.execute(text("ALTER TABLE platform_uploads ADD COLUMN reconciliation_metadata TEXT"))
                conn.commit()
        if "publishing_jobs" in inspector.get_table_names():
            pj_cols = [c["name"] for c in inspector.get_columns("publishing_jobs")]
            with db_engine.connect() as conn:
                if "worker_id" not in pj_cols:
                    conn.execute(text("ALTER TABLE publishing_jobs ADD COLUMN worker_id VARCHAR(100)"))
                if "locked_at" not in pj_cols:
                    conn.execute(text("ALTER TABLE publishing_jobs ADD COLUMN locked_at DATETIME"))
                if "lock_expires_at" not in pj_cols:
                    conn.execute(text("ALTER TABLE publishing_jobs ADD COLUMN lock_expires_at DATETIME"))
                if "allow_republish" not in pj_cols:
                    conn.execute(text("ALTER TABLE publishing_jobs ADD COLUMN allow_republish BOOLEAN DEFAULT 0"))
                conn.commit()
        if "clip_metadata" in inspector.get_table_names():
            cm_cols = [c["name"] for c in inspector.get_columns("clip_metadata")]
            with db_engine.connect() as conn:
                if "detected_game" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN detected_game VARCHAR(100) DEFAULT 'Unknown Game'"))
                if "game_confidence" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN game_confidence FLOAT DEFAULT 0.0"))
                if "game_franchise" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN game_franchise VARCHAR(100)"))
                if "game_genre" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN game_genre VARCHAR(100)"))
                if "gameplay_summary" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN gameplay_summary TEXT"))
                if "detected_events_json" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN detected_events_json TEXT"))
                if "candidate_titles_json" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN candidate_titles_json TEXT"))
                if "analysis_provider" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN analysis_provider VARCHAR(50) DEFAULT 'heuristic'"))
                if "analysis_status" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN analysis_status VARCHAR(50) DEFAULT 'complete'"))
                if "analysis_fingerprint" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN analysis_fingerprint VARCHAR(64)"))
                if "user_game_name" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN user_game_name VARCHAR(150)"))
                if "game_mismatch_flag" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN game_mismatch_flag BOOLEAN DEFAULT 0"))
                if "seo_score" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN seo_score FLOAT DEFAULT 0.0"))
                if "metadata_confidence" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN metadata_confidence FLOAT DEFAULT 0.0"))
                if "metadata_version" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN metadata_version INTEGER DEFAULT 1"))
                if "primary_keyword" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN primary_keyword VARCHAR(150)"))
                if "secondary_keywords_json" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN secondary_keywords_json TEXT"))
                if "facebook_title" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN facebook_title VARCHAR(255)"))
                if "instagram_alt_text" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN instagram_alt_text TEXT"))
                if "youtube_tags_json" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN youtube_tags_json TEXT"))
                if "thumbnail_text" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN thumbnail_text VARCHAR(150)"))
                if "original_metadata_json" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN original_metadata_json TEXT"))
                if "duplicate_detection_json" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN duplicate_detection_json TEXT"))
                if "is_manually_edited" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN is_manually_edited BOOLEAN DEFAULT 0"))
                if "opening_hook_alternatives_json" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN opening_hook_alternatives_json TEXT"))
                if "quality_gate_passed" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN quality_gate_passed BOOLEAN DEFAULT 1"))
                if "quality_gate_reasons_json" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN quality_gate_reasons_json TEXT"))
                if "updated_at" not in cm_cols:
                    conn.execute(text("ALTER TABLE clip_metadata ADD COLUMN updated_at DATETIME"))
                conn.commit()
        if "generated_clips" in inspector.get_table_names():
            gc_cols = [c["name"] for c in inspector.get_columns("generated_clips")]
            with db_engine.connect() as conn:
                if "hook_score" not in gc_cols:
                    conn.execute(text("ALTER TABLE generated_clips ADD COLUMN hook_score FLOAT"))
                if "hook_analysis_json" not in gc_cols:
                    conn.execute(text("ALTER TABLE generated_clips ADD COLUMN hook_analysis_json TEXT"))
                if "candidate_cuts_json" not in gc_cols:
                    conn.execute(text("ALTER TABLE generated_clips ADD COLUMN candidate_cuts_json TEXT"))
                conn.commit()
        if "publishing_analytics" in inspector.get_table_names():
            pa_cols = [c["name"] for c in inspector.get_columns("publishing_analytics")]
            with db_engine.connect() as conn:
                if "engaged_views" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN engaged_views INTEGER"))
                if "shown_in_feed" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN shown_in_feed INTEGER"))
                if "stayed_to_watch" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN stayed_to_watch INTEGER"))
                if "stayed_to_watch_pct" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN stayed_to_watch_pct FLOAT"))
                if "average_view_duration_sec" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN average_view_duration_sec FLOAT"))
                if "average_percentage_viewed" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN average_percentage_viewed FLOAT"))
                if "watch_time_minutes" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN watch_time_minutes FLOAT"))
                if "subscribers_gained" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN subscribers_gained INTEGER"))
                if "traffic_sources_json" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN traffic_sources_json TEXT"))
                if "is_measured" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN is_measured BOOLEAN DEFAULT 1"))
                if "is_analytics_api" not in pa_cols:
                    conn.execute(text("ALTER TABLE publishing_analytics ADD COLUMN is_analytics_api BOOLEAN DEFAULT 0"))
                conn.commit()
        if "source_videos" in inspector.get_table_names():
            sv_cols = [c["name"] for c in inspector.get_columns("source_videos")]
            with db_engine.connect() as conn:
                if "user_game_name" not in sv_cols:
                    conn.execute(text("ALTER TABLE source_videos ADD COLUMN user_game_name VARCHAR(150)"))
                conn.commit()
        if "source_authorizations" in inspector.get_table_names():
            sa_cols = [c["name"] for c in inspector.get_columns("source_authorizations")]
            with db_engine.connect() as conn:
                if "discovered_video_id" not in sa_cols:
                    conn.execute(text("ALTER TABLE source_authorizations ADD COLUMN discovered_video_id INTEGER"))
                if "source_owner" not in sa_cols:
                    conn.execute(text("ALTER TABLE source_authorizations ADD COLUMN source_owner VARCHAR(255)"))
                if "permitted_scope" not in sa_cols:
                    conn.execute(text("ALTER TABLE source_authorizations ADD COLUMN permitted_scope VARCHAR(255) DEFAULT 'shorts_clips_derivative'"))
                if "evidence_url" not in sa_cols:
                    conn.execute(text("ALTER TABLE source_authorizations ADD COLUMN evidence_url TEXT"))
                conn.commit()

        # Sanitize any legacy fabricated Instagram URLs (e.g. /reel/180...) and restore genuine state
        if "platform_uploads" in inspector.get_table_names():
            with db_engine.connect() as conn:
                conn.execute(text("""
                    UPDATE platform_uploads
                    SET external_url = NULL,
                        status = 'published_unverified',
                        error_code = 'VERIFICATION_PENDING',
                        error_message = 'Reset fabricated permalink. Awaiting genuine verification.'
                    WHERE platform = 'instagram'
                      AND external_url LIKE '%/reel/180%'
                """))
                if "publishing_idempotency_records" in inspector.get_table_names():
                    conn.execute(text("""
                        UPDATE publishing_idempotency_records
                        SET external_url = NULL,
                            status = 'PUBLISHED_UNVERIFIED'
                        WHERE platform = 'instagram'
                          AND external_url LIKE '%/reel/180%'
                    """))
                # Restore Job 29 to its genuine container
                conn.execute(text("""
                    UPDATE platform_uploads
                    SET external_post_id = '18058887653607108',
                        status = 'reconciling',
                        error_code = 'RECONCILING',
                        error_message = 'Recovered original in-progress container 18058887653607108'
                    WHERE publishing_job_id = 29 AND platform = 'instagram'
                """))
                conn.commit()

        if "system_settings" in inspector.get_table_names():
            with db_engine.connect() as conn:
                # Migrate legacy hardcoded 15/25 limits to NULL (Unlimited by default)
                conn.execute(text("""
                    UPDATE system_settings
                    SET daily_publishing_target = NULL
                    WHERE daily_publishing_target = 15
                """))
                conn.execute(text("""
                    UPDATE system_settings
                    SET youtube_daily_limit = NULL
                    WHERE youtube_daily_limit = 15
                """))
                conn.execute(text("""
                    UPDATE system_settings
                    SET instagram_daily_limit = NULL
                    WHERE instagram_daily_limit = 25
                """))
                conn.execute(text("""
                    UPDATE system_settings
                    SET facebook_daily_limit = NULL
                    WHERE facebook_daily_limit = 25
                """))
                conn.commit()

        # Automatically re-evaluate and recover any jobs paused exclusively by application daily quota
        try:
            from backend.app.publishers.quota_tracker import recover_quota_paused_jobs
            with SessionLocal() as db_session:
                recover_quota_paused_jobs(db_session)
        except Exception as rec_err:
            import logging
            logging.getLogger("gaming_uncut.db").warning(f"Quota recovery warning: {rec_err}")

        # Ensure all models are registered on metadata before create_all
        import backend.app.models.schema  # noqa: F401
        Base.metadata.create_all(bind=db_engine)
    except Exception as e:
        import logging
        logging.getLogger("gaming_uncut.db").warning(f"Schema compatibility check warning: {e}")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
