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
