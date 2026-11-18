"""
Core YouTube Gameplay Discovery & Lifecycle Management Engine.
Coordinates manual search, persistent scheduled discovery, source authorization,
duplicate prevention, quota compliance, and seamless handoff into the AI clipping pipeline.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    DiscoverySearch,
    DiscoverySource,
    DiscoveryChannel,
    DiscoverySchedule,
    DiscoveryJob,
    DiscoveredVideo,
    SourceAuthorization,
    SourceVideo,
    ProcessingJob,
    GeneratedClip,
    PublishingJob,
    SystemLog,
    PlatformQuotaState
)
from backend.app.discovery.client import (
    youtube_discovery_client,
    YouTubeDiscoveryError,
    YouTubeDiscoveryConfigError
)
from backend.app.publishers.quota_tracker import YouTubeQuotaExceededError, get_or_create_quota_state

logger = logging.getLogger("gaming_uncut.discovery.service")

class DiscoveryService:
    def __init__(self):
        pass

    # -------------------------------------------------------------------------
    # 1. Schedule & Configuration Management
    # -------------------------------------------------------------------------

    def get_or_create_schedule(self, db: Session) -> DiscoverySchedule:
        """Retrieves or initializes the persistent discovery schedule singleton."""
        schedule = db.query(DiscoverySchedule).first()
        if not schedule:
            schedule = DiscoverySchedule(
                name="Global YouTube Gameplay Discovery",
                interval_minutes=180,  # Every 3 hours
                is_active=True,
                max_discoveries_per_scan=10,
                max_videos_processed_per_day=5,
                max_clips_generated_per_day=15,
                ai_budget_daily=100,
                auto_approve_clips=False,
                auto_publish=False,  # CRITICAL REQUIREMENT: Automatic publishing disabled by default
                last_scan_status="idle",
                next_scan_at=datetime.utcnow() + timedelta(minutes=180)
            )
            db.add(schedule)
            db.commit()
            db.refresh(schedule)
        return schedule

    def update_schedule_settings(self, db: Session, updates: Dict[str, Any]) -> DiscoverySchedule:
        """Updates automation settings with strict validation."""
        schedule = self.get_or_create_schedule(db)
        if "interval_minutes" in updates:
            mins = max(int(updates["interval_minutes"]), 15)  # Min 15 mins
            schedule.interval_minutes = mins
            schedule.next_scan_at = datetime.utcnow() + timedelta(minutes=mins)
        if "is_active" in updates:
            schedule.is_active = bool(updates["is_active"])
        if "max_discoveries_per_scan" in updates:
            schedule.max_discoveries_per_scan = max(1, min(int(updates["max_discoveries_per_scan"]), 50))
        if "max_videos_processed_per_day" in updates:
            schedule.max_videos_processed_per_day = max(1, int(updates["max_videos_processed_per_day"]))
        if "max_clips_generated_per_day" in updates:
            schedule.max_clips_generated_per_day = max(1, int(updates["max_clips_generated_per_day"]))
        if "ai_budget_daily" in updates:
            schedule.ai_budget_daily = max(10, int(updates["ai_budget_daily"]))
        if "auto_approve_clips" in updates:
            schedule.auto_approve_clips = bool(updates["auto_approve_clips"])
        if "auto_publish" in updates:
            schedule.auto_publish = bool(updates["auto_publish"])

        schedule.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(schedule)
        return schedule

    def pause_schedule(self, db: Session) -> DiscoverySchedule:
        schedule = self.get_or_create_schedule(db)
        schedule.is_active = False
        schedule.last_scan_status = "paused"
        schedule.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(schedule)
        self._log_audit(db, "INFO", "Automatic YouTube discovery schedule paused by user.")
        return schedule

    def resume_schedule(self, db: Session) -> DiscoverySchedule:
        schedule = self.get_or_create_schedule(db)
        schedule.is_active = True
        schedule.last_scan_status = "idle"
        schedule.next_scan_at = datetime.utcnow() + timedelta(minutes=schedule.interval_minutes)
        schedule.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(schedule)
        self._log_audit(db, "INFO", "Automatic YouTube discovery schedule resumed.")
        return schedule

    # -------------------------------------------------------------------------
    # 2. Monitored Sources (Games) & Approved Channels
    # -------------------------------------------------------------------------

    def list_sources(self, db: Session) -> List[DiscoverySource]:
        return db.query(DiscoverySource).order_by(DiscoverySource.priority.desc(), DiscoverySource.id.asc()).all()

    def add_or_update_source(
        self,
        db: Session,
        game_title: str,
        name: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        is_active: bool = True,
        min_duration_sec: float = 60.0,
        max_duration_sec: float = 1800.0,
        min_views: int = 0,
        max_results_per_scan: int = 10,
        priority: int = 1,
        source_id: Optional[int] = None
    ) -> DiscoverySource:
        src = None
        if source_id:
            src = db.query(DiscoverySource).filter_by(id=source_id).first()

        if not src:
            src = DiscoverySource(
                name=name or f"{game_title} Highlights",
                game_title=game_title.strip(),
                keywords_json=json.dumps(keywords or ["gameplay", "highlights", "clutch"]),
                is_active=is_active,
                min_duration_sec=min_duration_sec,
                max_duration_sec=max_duration_sec,
                min_views=min_views,
                max_results_per_scan=max_results_per_scan,
                priority=priority,
                next_scan_at=datetime.utcnow()
            )
            db.add(src)
        else:
            if name:
                src.name = name
            src.game_title = game_title.strip()
            if keywords is not None:
                src.keywords_json = json.dumps(keywords)
            src.is_active = is_active
            src.min_duration_sec = min_duration_sec
            src.max_duration_sec = max_duration_sec
            src.min_views = min_views
            src.max_results_per_scan = max_results_per_scan
            src.priority = priority
            src.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(src)
        return src

    def delete_source(self, db: Session, source_id: int) -> bool:
        src = db.query(DiscoverySource).filter_by(id=source_id).first()
        if src:
            db.delete(src)
            db.commit()
            return True
        return False

    def list_channels(self, db: Session) -> List[DiscoveryChannel]:
        return db.query(DiscoveryChannel).order_by(DiscoveryChannel.id.desc()).all()

    def add_or_update_channel(
        self,
        db: Session,
        channel_id: str,
        channel_title: str,
        custom_url: Optional[str] = None,
        is_approved: bool = True,
        auto_authorize_sources: bool = False,
        notes: Optional[str] = None
    ) -> DiscoveryChannel:
        chan = db.query(DiscoveryChannel).filter_by(channel_id=channel_id).first()
        if not chan:
            chan = DiscoveryChannel(
                channel_id=channel_id.strip(),
                channel_title=channel_title.strip(),
                custom_url=custom_url,
                is_approved=is_approved,
                auto_authorize_sources=auto_authorize_sources,
                notes=notes
            )
            db.add(chan)
        else:
            chan.channel_title = channel_title.strip()
            if custom_url is not None:
                chan.custom_url = custom_url
            chan.is_approved = is_approved
            chan.auto_authorize_sources = auto_authorize_sources
            if notes is not None:
                chan.notes = notes
            chan.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(chan)
        return chan

    def delete_channel(self, db: Session, channel_id_or_pk: str) -> bool:
        chan = None
        if str(channel_id_or_pk).isdigit():
            chan = db.query(DiscoveryChannel).filter_by(id=int(channel_id_or_pk)).first()
        if not chan:
            chan = db.query(DiscoveryChannel).filter_by(channel_id=str(channel_id_or_pk)).first()
        if chan:
            db.delete(chan)
            db.commit()
            return True
        return False

    # -------------------------------------------------------------------------
    # 3. Manual Search Interface
    # -------------------------------------------------------------------------

    def search_manual(
        self,
        db: Session,
        query: str = "",
        game_titles: Optional[List[str]] = None,
        channel_id: Optional[str] = None,
        published_after: Optional[datetime] = None,
        duration_filter: Optional[str] = None,
        min_views: Optional[int] = 0,
        page_token: Optional[str] = None,
        max_results: int = 15
    ) -> Dict[str, Any]:
        """
        Executes a manual search using the YouTube Data API v3.
        Supports multi-game filtering, records a DiscoverySearch audit entry,
        and annotates returned results with their current discovery queue status.
        """
        primary_game = game_titles[0] if (game_titles and len(game_titles) == 1) else None
        search_rec = DiscoverySearch(
            query=query or ("Multi-game search" if game_titles else "General gameplay"),
            game_title=", ".join(game_titles) if game_titles else primary_game,
            search_type="manual",
            status="in_progress",
            created_at=datetime.utcnow()
        )
        db.add(search_rec)
        db.commit()

        try:
            # If multiple games provided, augment query
            effective_query = query
            if game_titles and len(game_titles) > 1:
                games_str = " OR ".join([f'"{g}"' for g in game_titles])
                effective_query = f"({games_str}) {query}".strip()

            search_res = youtube_discovery_client.search_videos(
                query=effective_query,
                game_title=primary_game,
                channel_id=channel_id,
                published_after=published_after,
                duration_filter=duration_filter,
                max_results=max_results,
                page_token=page_token,
                db=db
            )

            items = search_res.get("items", [])
            # Filter by min_views if requested (safely handle None or <= 0)
            views_thresh = int(min_views) if (min_views is not None and min_views > 0) else 0
            if views_thresh > 0:
                items = [it for it in items if it.get("view_count", 0) >= views_thresh]

            # Cross-reference existing DiscoveredVideo records to show current status
            video_ids = [it["youtube_video_id"] for it in items]
            existing_videos = {
                dv.youtube_video_id: dv
                for dv in db.query(DiscoveredVideo).filter(DiscoveredVideo.youtube_video_id.in_(video_ids)).all()
            }

            annotated_items = []
            for it in items:
                vid = it["youtube_video_id"]
                existing = existing_videos.get(vid)
                if existing:
                    it["in_queue"] = True
                    it["queue_status"] = existing.status
                    it["discovered_video_id"] = existing.id
                    it["authorization_state"] = (
                        "authorized" if existing.status not in ["authorization_required", "discovered"]
                        else "authorization_required"
                    )
                else:
                    it["in_queue"] = False
                    it["queue_status"] = "discovered"
                    it["discovered_video_id"] = None
                    it["authorization_state"] = "authorization_required"
                annotated_items.append(it)

            search_rec.status = "completed"
            search_rec.results_count = len(annotated_items)
            search_rec.quota_units_used = search_res.get("quota_used", 101)
            db.commit()

            return {
                "items": annotated_items,
                "next_page_token": search_res.get("next_page_token"),
                "prev_page_token": search_res.get("prev_page_token"),
                "total_results": search_res.get("total_results", len(annotated_items)),
                "search_id": search_rec.id,
                "quota_used": search_rec.quota_units_used
            }

        except Exception as e:
            search_rec.status = "failed"
            search_rec.error_message = str(e)
            db.commit()
            self._log_audit(db, "ERROR", f"Manual YouTube discovery search failed: {e}")
            raise

    # -------------------------------------------------------------------------
    # 4. Import to Discovery Queue & Authorization
    # -------------------------------------------------------------------------

    def import_videos_to_queue(
        self,
        db: Session,
        videos: List[Dict[str, Any]],
        discovery_mode: str = "manual",
        search_id: Optional[int] = None,
        job_id: Optional[int] = None,
        auto_authorize: bool = False
    ) -> List[DiscoveredVideo]:
        """
        Imports videos into the unified Discovery Queue.
        Stores source provenance once per video.
        """
        imported: List[DiscoveredVideo] = []

        # Load allowlisted channels that auto-authorize
        auto_auth_channels = {
            c.channel_id: c
            for c in db.query(DiscoveryChannel).filter_by(is_approved=True, auto_authorize_sources=True).all()
        }

        for v_data in videos:
            vid = v_data.get("youtube_video_id")
            if not vid:
                continue

            # Deduplication: Check if already exists in DiscoveredVideo
            existing = db.query(DiscoveredVideo).filter_by(youtube_video_id=vid).first()
            if existing:
                # Update metadata if needed, but do not re-import
                if v_data.get("view_count"):
                    existing.view_count = v_data["view_count"]
                if v_data.get("duration_sec"):
                    existing.duration_sec = v_data["duration_sec"]
                if auto_authorize and existing.status in ["authorization_required", "discovered"]:
                    existing.status = "authorized"
                    if not existing.authorization:
                        auth = SourceAuthorization(
                            discovered_video_id=existing.id,
                            auth_type="creator_authorized",
                            authorized_by=v_data.get("channel_title", "Channel Owner"),
                            source_owner=v_data.get("channel_title"),
                            permitted_scope="shorts_clips_derivative",
                            notes="Auto-authorized via simplified discovery workflow",
                            confirmed_at=datetime.utcnow()
                        )
                        db.add(auth)
                imported.append(existing)
                continue

            # Check if channel is approved for auto-authorization or auto_authorize requested
            chan_id = v_data.get("channel_id")
            is_auto_auth = (chan_id in auto_auth_channels if chan_id else False) or auto_authorize

            initial_status = "authorized" if is_auto_auth else "authorization_required"

            # Parse published_at safely into a naive UTC datetime
            pub_raw = v_data.get("published_at")
            pub_dt = None
            if isinstance(pub_raw, datetime):
                pub_dt = pub_raw.replace(tzinfo=None) if pub_raw.tzinfo else pub_raw
            elif isinstance(pub_raw, str) and pub_raw.strip():
                try:
                    parsed_dt = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                    pub_dt = parsed_dt.astimezone(timezone.utc).replace(tzinfo=None) if parsed_dt.tzinfo else parsed_dt
                except Exception:
                    pub_dt = None

            dur_sec = 0.0
            try:
                dur_sec = float(v_data.get("duration_sec", 0.0) or 0.0)
            except (ValueError, TypeError):
                dur_sec = 0.0

            views = 0
            try:
                views = int(v_data.get("view_count", 0) or 0)
            except (ValueError, TypeError):
                views = 0

            d_video = DiscoveredVideo(
                youtube_video_id=vid,
                title=v_data.get("title", f"YouTube {vid}"),
                channel_id=chan_id,
                channel_title=v_data.get("channel_title", "YouTube Channel"),
                published_at=pub_dt,
                duration_sec=dur_sec,
                view_count=views,
                thumbnail_url=v_data.get("thumbnail_url"),
                youtube_url=v_data.get("youtube_url") or f"https://www.youtube.com/watch?v={vid}",
                game_title=v_data.get("game_title", "Unknown Game"),
                discovery_mode=discovery_mode,
                discovery_search_id=search_id,
                discovery_job_id=job_id,
                status=initial_status,
                created_at=datetime.utcnow()
            )
            db.add(d_video)
            db.flush()

            # Record SourceAuthorization provenance
            if is_auto_auth:
                auth = SourceAuthorization(
                    discovered_video_id=d_video.id,
                    auth_type="creator_authorized",
                    authorized_by=v_data.get("channel_title", "Channel Owner"),
                    source_owner=v_data.get("channel_title"),
                    permitted_scope="shorts_clips_derivative",
                    notes=f"Authorized source provenance recorded for: {chan_id or vid}",
                    confirmed_at=datetime.utcnow()
                )
                db.add(auth)

            imported.append(d_video)

        db.commit()
        for item in imported:
            db.refresh(item)

        self._log_audit(
            db,
            "INFO",
            f"Imported {len(imported)} discovered videos into queue (mode: {discovery_mode})."
        )
        return imported

    def authorize_source(
        self,
        db: Session,
        discovered_video_id: int,
        auth_type: str = "creator_authorized",
        authorized_by: Optional[str] = None,
        source_owner: Optional[str] = None,
        permitted_scope: str = "shorts_clips_derivative",
        evidence_url: Optional[str] = None,
        notes: Optional[str] = None,
        local_file_path: Optional[str] = None,
        enqueue_now: bool = True
    ) -> DiscoveredVideo:
        """
        Attaches source authorization to a discovered video.
        Enables acquisition via user-uploaded file, local file path, or explicit creator consent.
        """
        d_video = db.query(DiscoveredVideo).filter_by(id=discovered_video_id).first()
        if not d_video:
            raise ValueError(f"Discovered video #{discovered_video_id} not found.")

        # Check local file if specified
        if local_file_path and local_file_path.strip():
            p = Path(local_file_path.strip()).resolve()
            if not p.exists() or not p.is_file():
                raise FileNotFoundError(f"Authorized source video file does not exist on disk: '{p}'")

        # Create or update authorization
        auth = db.query(SourceAuthorization).filter_by(discovered_video_id=d_video.id).first()
        if not auth:
            auth = SourceAuthorization(
                discovered_video_id=d_video.id,
                auth_type=auth_type,
                authorized_by=authorized_by or "Creator",
                source_owner=source_owner or d_video.channel_title,
                permitted_scope=permitted_scope,
                evidence_url=evidence_url,
                notes=notes,
                confirmed_at=datetime.utcnow()
            )
            db.add(auth)
        else:
            auth.auth_type = auth_type
            auth.authorized_by = authorized_by or auth.authorized_by
            auth.source_owner = source_owner or auth.source_owner
            auth.permitted_scope = permitted_scope
            auth.evidence_url = evidence_url
            auth.notes = notes
            auth.confirmed_at = datetime.utcnow()

        d_video.status = "authorized"
        d_video.error_message = None
        d_video.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(d_video)

        self._log_audit(
            db,
            "INFO",
            f"Source authorization granted for discovered video #{d_video.id} ('{d_video.title[:40]}...'). Scope: {permitted_scope}."
        )

        if enqueue_now:
            self.enqueue_for_processing(db, d_video.id, local_file_path=local_file_path)

        return d_video

    def enqueue_for_processing(
        self,
        db: Session,
        discovered_video_id: int,
        local_file_path: Optional[str] = None,
        auto_authorize: bool = False,
        game_name: Optional[str] = None
    ) -> ProcessingJob:
        """
        Dispatches an authorized discovered video into the AI Processing Pipeline.
        Enforces authorization check: blocks unauthorized videos unless auto_authorize is True.
        """
        d_video = db.query(DiscoveredVideo).filter_by(id=discovered_video_id).first()
        if not d_video:
            raise ValueError(f"Discovered video #{discovered_video_id} not found.")

        # Automatically store source provenance once if auto_authorize requested
        if auto_authorize and d_video.status in ["authorization_required", "discovered", "failed"]:
            auth = db.query(SourceAuthorization).filter_by(discovered_video_id=d_video.id).first()
            if not auth:
                auth = SourceAuthorization(
                    discovered_video_id=d_video.id,
                    auth_type="creator_authorized",
                    authorized_by=d_video.channel_title or "Creator",
                    source_owner=d_video.channel_title or "Channel Owner",
                    permitted_scope="shorts_clips_derivative",
                    notes="Curated via YouTube Gameplay Discovery",
                    confirmed_at=datetime.utcnow()
                )
                db.add(auth)
            d_video.status = "authorized"
            d_video.error_message = None
            db.commit()

        if d_video.status in ["authorization_required", "discovered"]:
            raise PermissionError(
                f"Cannot process video #{discovered_video_id}: Source authorization required. "
                "YouTube search results cannot be downloaded without explicit creator authorization."
            )

        # Check daily video processing limit
        schedule = self.get_or_create_schedule(db)
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        processed_today = db.query(DiscoveredVideo).filter(
            DiscoveredVideo.status.in_(["queued", "analyzing", "highlights_detected", "rendering", "ready_to_publish", "published"]),
            DiscoveredVideo.updated_at >= today_start
        ).count()

        if processed_today >= schedule.max_videos_processed_per_day:
            raise ValueError(
                f"Daily processing target reached ({processed_today}/{schedule.max_videos_processed_per_day} videos). "
                "Increase limit in Automation Settings or wait until tomorrow."
            )

        # Build target path or URL
        input_target = local_file_path if (local_file_path and Path(local_file_path).exists()) else d_video.youtube_url

        from backend.app.services.pipeline_service import pipeline_service

        # Initialize or link SourceVideo
        existing_src = db.query(SourceVideo).filter(
            or_(SourceVideo.url == d_video.youtube_url, SourceVideo.local_path == str(input_target))
        ).first()

        if not existing_src:
            existing_src = SourceVideo(
                url=d_video.youtube_url,
                title=d_video.title,
                duration=d_video.duration_sec,
                local_path=str(input_target),
                source_type="local" if local_file_path else "youtube",
                author=d_video.channel_title,
                license_info="Creator Authorized Ingestion",
                status="pending_download" if not local_file_path else "ready"
            )
            db.add(existing_src)
            db.flush()

        # Link SourceAuthorization to source_video_id
        auth = db.query(SourceAuthorization).filter_by(discovered_video_id=d_video.id).first()
        if auth and not auth.source_video_id:
            auth.source_video_id = existing_src.id

        # Create ProcessingJob
        job = ProcessingJob(
            source_video_id=existing_src.id,
            job_type="full_pipeline",
            status="queued",
            progress_pct=5,
            current_step="Discovery queue dispatch",
            created_at=datetime.utcnow()
        )
        db.add(job)
        db.flush()

        d_video.source_video_id = existing_src.id
        d_video.processing_job_id = job.id
        d_video.status = "queued"
        d_video.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(job)

        # Trigger async pipeline task with publish_mode="approval" to separate clipping from publishing
        pipeline_service.start_job(
            job_id=job.id,
            url_or_path=str(input_target),
            auth_type="creator_authorized",
            approval_mode="manual",
            publish_mode="approval",
            game_name=game_name or d_video.game_title
        )

        self._log_audit(
            db,
            "INFO",
            f"Discovered video #{d_video.id} ('{d_video.title[:40]}...') enqueued for AI production as Job #{job.id}."
        )
        return job

    def create_clips_for_videos(
        self,
        db: Session,
        video_ids: Optional[List[int]] = None,
        videos: Optional[List[Dict[str, Any]]] = None,
        local_file_paths: Optional[Dict[str, str]] = None,
        target_duration: int = 60,
        game_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Streamlined Clipping Workflow:
        - Automatically stores source provenance once.
        - Removes the intrusive authorization popup modal.
        - Enqueues selected videos for AI highlight detection, NVENC rendering & SEO metadata.
        - Strictly separates clipping from publishing (clips stored in Content Library).
        """
        local_paths_map = local_file_paths or {}
        queued_records = []

        # 1. If raw search item dictionaries provided, import them
        target_vids = []
        if videos and len(videos) > 0:
            imported = self.import_videos_to_queue(db, videos, discovery_mode="manual", auto_authorize=True)
            for v in imported:
                if v.id not in target_vids:
                    target_vids.append(v.id)

        # 2. Add explicit IDs
        if video_ids:
            for vid in video_ids:
                if vid not in target_vids:
                    target_vids.append(vid)

        if not target_vids:
            raise ValueError("No eligible gameplay videos provided for clipping.")

        # 3. Queue clipping job for each video
        for v_id in target_vids:
            d_video = db.query(DiscoveredVideo).filter_by(id=v_id).first()
            if not d_video:
                continue

            l_path = local_paths_map.get(str(v_id)) or local_paths_map.get(d_video.youtube_video_id)

            try:
                job = self.enqueue_for_processing(
                    db=db,
                    discovered_video_id=v_id,
                    local_file_path=l_path,
                    auto_authorize=True,
                    game_name=game_name or d_video.game_title
                )
                queued_records.append({
                    "video_id": d_video.id,
                    "youtube_video_id": d_video.youtube_video_id,
                    "job_id": job.id,
                    "title": d_video.title,
                    "channel_title": d_video.channel_title,
                    "game_title": d_video.game_title,
                    "status": "queued",
                    "source_type": "local" if l_path else "youtube"
                })
            except Exception as e:
                logger.warning(f"Could not enqueue discovered video #{v_id} for clipping: {e}")
                queued_records.append({
                    "video_id": d_video.id,
                    "youtube_video_id": d_video.youtube_video_id,
                    "job_id": None,
                    "title": d_video.title,
                    "channel_title": d_video.channel_title,
                    "game_title": d_video.game_title,
                    "status": "failed",
                    "error": str(e)
                })

        return queued_records

    # -------------------------------------------------------------------------
    # 5. Scheduled Automatic Discovery Engine
    # -------------------------------------------------------------------------

    def execute_scheduled_discovery(self, db: Session, force: bool = False) -> Optional[DiscoveryJob]:
        """
        Executes an automatic scheduled discovery scan across all active monitored games.
        - Respects scan interval and pause states.
        - Checks daily quota limits.
        - Deduplicates against existing footage.
        - Records persistent checkpoints.
        """
        schedule = self.get_or_create_schedule(db)
        now = datetime.utcnow()

        if not force:
            if not schedule.is_active:
                logger.debug("Scheduled discovery is paused or inactive.")
                return None
            if schedule.next_scan_at and schedule.next_scan_at > now:
                logger.debug(f"Discovery scan not due yet (next scan: {schedule.next_scan_at}).")
                return None

        # Check quota availability before initiating scan
        quota_state = get_or_create_quota_state(db, "youtube")
        if quota_state.api_quota_exceeded and quota_state.pause_until and quota_state.pause_until > now:
            schedule.last_scan_status = "paused_quota"
            db.commit()
            logger.warning(f"Skipping scheduled discovery: YouTube API quota paused until {quota_state.pause_until} UTC.")
            return None

        # Active monitored sources
        sources = db.query(DiscoverySource).filter_by(is_active=True).order_by(DiscoverySource.priority.desc()).all()
        if not sources:
            logger.info("No active monitored gaming sources configured for automatic discovery.")
            schedule.last_scan_at = now
            schedule.next_scan_at = now + timedelta(minutes=schedule.interval_minutes)
            schedule.last_scan_status = "idle"
            db.commit()
            return None

        job = DiscoveryJob(
            schedule_id=schedule.id,
            status="running",
            started_at=now,
            created_at=now
        )
        db.add(job)
        schedule.last_scan_status = "running"
        db.commit()
        db.refresh(job)

        total_found = 0
        total_imported = 0
        total_quota = 0

        try:
            for src in sources:
                keywords_list = []
                try:
                    keywords_list = json.loads(src.keywords_json) if src.keywords_json else []
                except Exception:
                    pass
                kw_str = " ".join(keywords_list[:3]) if keywords_list else "gameplay"

                # Checkpoint: published after last scan or past 48h
                published_after = src.last_scanned_at or (now - timedelta(hours=48))

                try:
                    res = youtube_discovery_client.search_videos(
                        query=kw_str,
                        game_title=src.game_title,
                        published_after=published_after,
                        max_results=src.max_results_per_scan or schedule.max_discoveries_per_scan,
                        db=db
                    )
                    items = res.get("items", [])
                    total_quota += res.get("quota_used", 101)
                    total_found += len(items)

                    # Filter by min/max duration and views
                    filtered = []
                    for it in items:
                        dur = it.get("duration_sec", 0)
                        views = it.get("view_count", 0)
                        if src.min_duration_sec and dur < src.min_duration_sec:
                            continue
                        if src.max_duration_sec and dur > src.max_duration_sec:
                            continue
                        if src.min_views and views < src.min_views:
                            continue
                        filtered.append(it)

                    imported_list = self.import_videos_to_queue(
                        db=db,
                        videos=filtered,
                        discovery_mode="automatic",
                        job_id=job.id
                    )
                    total_imported += len(imported_list)

                    src.last_scanned_at = now
                    src.next_scan_at = now + timedelta(minutes=schedule.interval_minutes)
                    db.commit()

                except YouTubeQuotaExceededError:
                    logger.warning("YouTube API daily quota exhausted during scheduled scan.")
                    schedule.last_scan_status = "paused_quota"
                    job.status = "paused_quota"
                    job.error_message = "YouTube API quota exhausted."
                    break
                except Exception as src_err:
                    logger.error(f"Error scanning source '{src.name}': {src_err}")
                    continue

            # Update job and schedule
            job.videos_found = total_found
            job.videos_imported = total_imported
            job.quota_used = total_quota
            job.completed_at = datetime.utcnow()
            if job.status != "paused_quota":
                job.status = "completed"

            schedule.last_scan_at = datetime.utcnow()
            schedule.next_scan_at = datetime.utcnow() + timedelta(minutes=schedule.interval_minutes)
            schedule.last_scan_status = "success" if job.status == "completed" else job.status
            db.commit()

            self._log_audit(
                db,
                "INFO",
                f"Scheduled discovery finished. Found {total_found} videos, imported {total_imported} into queue. Quota used: {total_quota}."
            )
            return job

        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.utcnow()
            schedule.last_scan_status = "failed"
            schedule.next_scan_at = datetime.utcnow() + timedelta(minutes=schedule.interval_minutes)
            db.commit()
            self._log_audit(db, "ERROR", f"Scheduled discovery job failed: {e}")
            raise

    # -------------------------------------------------------------------------
    # 6. Queue Retrieval & Item Operations
    # -------------------------------------------------------------------------

    def get_queue(
        self,
        db: Session,
        status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Tuple[List[DiscoveredVideo], int]:
        """Retrieves paginated discovered videos with optional filtering."""
        query = db.query(DiscoveredVideo)

        if status and status.upper() != "ALL":
            query = query.filter(DiscoveredVideo.status == status.lower())

        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    DiscoveredVideo.title.ilike(term),
                    DiscoveredVideo.channel_title.ilike(term),
                    DiscoveredVideo.game_title.ilike(term),
                    DiscoveredVideo.youtube_video_id.ilike(term)
                )
            )

        total = query.count()
        items = query.order_by(desc(DiscoveredVideo.id)).offset(offset).limit(limit).all()
        return items, total

    def retry_video(self, db: Session, video_id: int) -> DiscoveredVideo:
        d_video = db.query(DiscoveredVideo).filter_by(id=video_id).first()
        if not d_video:
            raise ValueError(f"Discovered video #{video_id} not found.")

        # If it was failed, return to authorized or authorization_required
        auth = db.query(SourceAuthorization).filter_by(discovered_video_id=d_video.id).first()
        d_video.status = "authorized" if auth else "authorization_required"
        d_video.error_message = None
        d_video.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(d_video)

        if auth:
            self.enqueue_for_processing(db, d_video.id)

        return d_video

    def skip_video(self, db: Session, video_id: int) -> DiscoveredVideo:
        d_video = db.query(DiscoveredVideo).filter_by(id=video_id).first()
        if not d_video:
            raise ValueError(f"Discovered video #{video_id} not found.")
        d_video.status = "skipped"
        d_video.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(d_video)
        return d_video

    def sync_pipeline_status(self, db: Session, job_id: int, stage_idx: int, stage_name: str, status: str):
        """Called by pipeline service to synchronize processing status with DiscoveredVideo."""
        d_video = db.query(DiscoveredVideo).filter_by(processing_job_id=job_id).first()
        if not d_video:
            return

        if status == "failed":
            d_video.status = "failed"
        elif status == "cancelled":
            d_video.status = "skipped"
        elif stage_idx in [2, 3]:
            d_video.status = "analyzing"
        elif stage_idx == 4:
            d_video.status = "highlights_detected"
        elif stage_idx in [5, 6]:
            d_video.status = "rendering"
        elif stage_idx >= 7 and status == "completed":
            schedule = self.get_or_create_schedule(db)
            d_video.status = "ready_to_publish" if schedule.auto_approve_clips else "review_required"
            # Count generated clips
            clips_count = db.query(GeneratedClip).filter_by(job_id=job_id).count()
            d_video.clips_count = clips_count

        d_video.updated_at = datetime.utcnow()
        db.commit()

    def _log_audit(self, db: Session, level: str, message: str):
        try:
            log = SystemLog(
                module="youtube_discovery",
                level=level.upper(),
                message=message,
                created_at=datetime.utcnow()
            )
            db.add(log)
            db.commit()
        except Exception:
            pass

discovery_service = DiscoveryService()
