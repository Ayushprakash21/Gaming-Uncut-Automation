"""
End-to-End Autonomous Pipeline Service for Gaming Uncut Automation.
Coordinates Ingestion -> Analysis -> Highlight Selection -> 60s Video Rendering ->
Quality Control -> AI Metadata -> Scheduled Multi-Platform Publishing.
"""

import os
import json
import time
import shutil
import logging
import threading
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Set, List

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    SourceVideo, ProcessingJob, DetectedHighlight, GeneratedClip, ClipMetadata,
    PublishingJob, PlatformUpload, SystemLog, ApplicationEvent
)
from backend.app.ingestion.adapters import ingestion_manager, normalize_youtube_url
from backend.app.ai.audio_analyzer import extract_pcm_wav, calculate_loudness_peaks
from backend.app.ai.scene_analyzer import detect_scene_cuts
from backend.app.ai.transcriber import transcribe_audio_words
from backend.app.ai.gemini_provider import gemini_provider
from backend.app.captions.builder import generate_animated_ass
from backend.app.video.renderer import video_renderer
from backend.app.video.validator import quality_validator
from backend.app.publishers.manager import publishing_manager

logger = logging.getLogger("gaming_uncut.pipeline")

class JobCancelledException(Exception):
    """Raised when an individual processing job is explicitly cancelled by the user."""
    pass

_ACTIVE_JOB_THREADS: Dict[int, threading.Thread] = {}
_ACTIVE_JOB_SUBPROCESSES: Dict[int, Set[subprocess.Popen]] = {}
_JOB_CANCELLATION_FLAGS: Dict[int, bool] = {}
_ACTIVE_JOB_LOCK = threading.Lock()

def kill_process_tree(proc: subprocess.Popen):
    """Cleanly and forcefully terminates a process and any child processes it spawned."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception as e:
        logger.warning(f"Error terminating process PID {getattr(proc, 'pid', None)}: {e}")

class PipelineService:
    STAGES = [
        "Source validation",
        "Media ingestion",
        "AI video analysis",
        "Highlight detection",
        "Clip composition",
        "Caption generation",
        "Video rendering",
        "Quality validation",
        "Metadata generation",
        "Reel ready"
    ]

    def register_active_job(self, job_id: int):
        with _ACTIVE_JOB_LOCK:
            _ACTIVE_JOB_THREADS[job_id] = threading.current_thread()
            _ACTIVE_JOB_SUBPROCESSES[job_id] = set()
            _JOB_CANCELLATION_FLAGS[job_id] = False

    def unregister_active_job(self, job_id: int):
        with _ACTIVE_JOB_LOCK:
            _ACTIVE_JOB_THREADS.pop(job_id, None)
            _ACTIVE_JOB_SUBPROCESSES.pop(job_id, None)
            _JOB_CANCELLATION_FLAGS.pop(job_id, None)

    def register_subprocess(self, job_id: int, proc: subprocess.Popen):
        with _ACTIVE_JOB_LOCK:
            if job_id not in _ACTIVE_JOB_SUBPROCESSES:
                _ACTIVE_JOB_SUBPROCESSES[job_id] = set()
            _ACTIVE_JOB_SUBPROCESSES[job_id].add(proc)

    def unregister_subprocess(self, job_id: int, proc: subprocess.Popen):
        with _ACTIVE_JOB_LOCK:
            if job_id in _ACTIVE_JOB_SUBPROCESSES:
                _ACTIVE_JOB_SUBPROCESSES[job_id].discard(proc)

    def terminate_job_subprocesses(self, job_id: int):
        """Immediately terminates all active child processes (e.g. FFmpeg, yt-dlp) for a specific job."""
        with _ACTIVE_JOB_LOCK:
            procs = list(_ACTIVE_JOB_SUBPROCESSES.get(job_id, []))
        for p in procs:
            if p.poll() is None:
                logger.info(f"Killing active subprocess PID {p.pid} for Job #{job_id}...")
                kill_process_tree(p)

    def is_job_running(self, job_id: int) -> bool:
        with _ACTIVE_JOB_LOCK:
            t = _ACTIVE_JOB_THREADS.get(job_id)
            return t is not None and t.is_alive()

    def is_cancellation_requested(self, job_id: int) -> bool:
        with _ACTIVE_JOB_LOCK:
            if _JOB_CANCELLATION_FLAGS.get(job_id, False):
                return True
        with SessionLocal() as db:
            j = db.query(ProcessingJob).filter_by(id=job_id).first()
            if j and (j.cancellation_requested or j.status in ("cancelling", "cancelled")):
                with _ACTIVE_JOB_LOCK:
                    _JOB_CANCELLATION_FLAGS[job_id] = True
                return True
        return False

    def check_cancellation(self, job_id: int):
        """Raises JobCancelledException immediately if cancellation was requested for this job."""
        if self.is_cancellation_requested(job_id):
            raise JobCancelledException(f"Processing cancelled by user for Job #{job_id}")

    def request_job_cancellation(self, job_id: int) -> bool:
        """
        Signals cancellation for a single job:
        - Sets in-memory cancellation flag.
        - Forcefully terminates any active subprocesses (FFmpeg / yt-dlp) running for this job.
        - Persists status in DB ('cancelling' if worker alive, 'cancelled' if already stopped).
        """
        with _ACTIVE_JOB_LOCK:
            _JOB_CANCELLATION_FLAGS[job_id] = True

        # Immediately terminate any active subprocesses executing for this job
        self.terminate_job_subprocesses(job_id)

        with SessionLocal() as db:
            job = db.query(ProcessingJob).filter_by(id=job_id).first()
            if not job:
                return False
            job.cancellation_requested = True
            
            if not self.is_job_running(job_id):
                job.status = "cancelled"
                job.current_step = "Cancelled"
                job.error_message = "Cancelled by user."
                job.completed_at = datetime.utcnow()
            else:
                job.status = "cancelling"
                job.error_message = "Cancellation requested by user."
            db.commit()
            return True

    def run_cancellable_subprocess(
        self,
        cmd: List[str],
        job_id: Optional[int] = None,
        timeout: Optional[float] = None,
        check: bool = True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs
    ) -> subprocess.CompletedProcess:
        """
        Runs a subprocess (e.g. FFmpeg) with real-time cancellation tracking.
        If cancellation is signaled for job_id, kills the process tree immediately and raises JobCancelledException.
        """
        if job_id and self.is_cancellation_requested(job_id):
            raise JobCancelledException(f"Job #{job_id} was cancelled before starting process: {cmd[0]}")

        proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, text=True, **kwargs)
        if job_id:
            self.register_subprocess(job_id, proc)

        start_time = time.time()
        try:
            while True:
                if job_id and self.is_cancellation_requested(job_id):
                    logger.info(f"Cancellation requested for Job #{job_id}: killing PID {proc.pid} ({cmd[0]})...")
                    kill_process_tree(proc)
                    raise JobCancelledException(f"Subprocess terminated due to cancellation of Job #{job_id}")

                if timeout and (time.time() - start_time) > timeout:
                    kill_process_tree(proc)
                    raise subprocess.TimeoutExpired(cmd, timeout)

                try:
                    out, err = proc.communicate(timeout=0.25)
                    ret = proc.returncode
                    if check and ret != 0:
                        raise subprocess.CalledProcessError(ret, cmd, output=out, stderr=err)
                    return subprocess.CompletedProcess(cmd, ret, out, err)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if job_id:
                self.unregister_subprocess(job_id, proc)

    def detect_and_recover_stalled_jobs(self, stale_threshold_seconds: int = 180) -> int:
        """
        Detects zombie processing jobs that were abandoned due to backend restart
        or thread crash, and transitions them to 'stalled' so they can be safely retried.
        """
        recovered = 0
        now = datetime.utcnow()
        with SessionLocal() as db:
            active_jobs = db.query(ProcessingJob).filter_by(status="processing").all()
            for job in active_jobs:
                if self.is_job_running(job.id):
                    continue

                last_time = job.last_heartbeat or job.started_at or job.created_at
                elapsed = (now - last_time).total_seconds() if last_time else 999999

                if elapsed > stale_threshold_seconds:
                    logger.warning(
                        f"Job #{job.id} stalled at '{job.current_step}' ({job.progress_pct}%) - "
                        f"no active worker and no heartbeat for {int(elapsed)}s. Marking as stalled."
                    )
                    job.status = "stalled"
                    job.error_message = f"Processing stalled: worker heartbeat timed out ({int(elapsed)}s elapsed without heartbeat or backend restarted)."
                    recovered += 1
            if recovered > 0:
                db.commit()
        return recovered

    def _update_job_stage(self, job_id: int, stage_idx: int, stage_name: str, progress_pct: int, status: str = "processing"):
        with SessionLocal() as db:
            db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
            if db_job:
                if db_job.cancellation_requested or db_job.status in ("cancelling", "cancelled") or self.is_cancellation_requested(job_id):
                    db_job.status = "cancelled"
                    db_job.current_step = "Cancelled"
                    db_job.error_message = "Cancelled by user request."
                    db_job.completed_at = datetime.utcnow()
                    db.commit()
                    raise JobCancelledException(f"Processing cancelled by user for Job #{job_id}")
                db_job.status = status
                db_job.progress_pct = progress_pct
                db_job.current_step = stage_name
                db_job.last_heartbeat = datetime.utcnow()
                db_job.worker_pid = os.getpid()
                db.commit()
                try:
                    from backend.app.discovery.service import discovery_service
                    discovery_service.sync_pipeline_status(db, job_id, stage_idx, stage_name, status)
                except Exception:
                    pass

    def start_job(
        self,
        job_id: int,
        url_or_path: str,
        template: str = "blurred_bg",
        publish_mode: str = "approval",
        platforms: Optional[List[str]] = None,
        target_duration: int = 60,
        resolution: str = "1080x1920",
        aspect_ratio: str = "9:16",
        enable_ai_highlights: bool = True,
        enable_smart_cropping: bool = True,
        enable_captions: bool = True,
        enable_watermark: bool = True,
        enable_ai_metadata: bool = True,
        background_tasks: Any = None,
        auth_type: str = "creator_authorized",
        approval_mode: str = "manual",
        game_name: Optional[str] = None
    ):
        """Asynchronously dispatches pipeline execution for a processing job."""
        effective_publish_mode = "immediate" if approval_mode == "auto" else publish_mode
        kwargs = {
            "url_or_path": url_or_path,
            "template": template,
            "publish_mode": effective_publish_mode,
            "platforms": platforms or ["youtube"],
            "job_id": job_id,
            "target_duration": target_duration,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "enable_ai_highlights": enable_ai_highlights,
            "enable_smart_cropping": enable_smart_cropping,
            "enable_captions": enable_captions,
            "enable_watermark": enable_watermark,
            "enable_ai_metadata": enable_ai_metadata,
            "game_name": game_name
        }
        if background_tasks:
            background_tasks.add_task(self.execute_from_url_or_path, **kwargs)
        else:
            import threading
            t = threading.Thread(target=self.execute_from_url_or_path, kwargs=kwargs, daemon=True)
            t.start()

    def execute_from_url_or_path(
        self,
        url_or_path: str,
        template: str = "blurred_bg",
        publish_mode: str = "approval",
        platforms: list[str] = None,
        job_id: Optional[int] = None,
        target_duration: int = 60,
        resolution: str = "1080x1920",
        aspect_ratio: str = "9:16",
        enable_ai_highlights: bool = True,
        enable_smart_cropping: bool = True,
        enable_captions: bool = True,
        enable_watermark: bool = True,
        enable_ai_metadata: bool = True,
        notes: Optional[str] = None,
        game_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes full 10-stage autonomous pipeline:
        1. Source validation
        2. Media ingestion
        3. AI video analysis
        4. Highlight detection
        5. Clip composition
        6. Caption generation
        7. Video rendering (NVENC 1080x1920)
        8. Quality validation
        9. Metadata generation
        10. Reel ready
        """
        if platforms is None:
            platforms = ["youtube", "instagram", "facebook"]

        # Normalize input URL if YouTube
        clean_target = normalize_youtube_url(url_or_path) if ("youtube.com" in url_or_path or "youtu.be" in url_or_path) else url_or_path.strip()

        # Ensure initial Job record exists
        if job_id is None:
            with SessionLocal() as db:
                sv = db.query(SourceVideo).filter(
                    (SourceVideo.url == clean_target) | (SourceVideo.local_path == clean_target)
                ).first()
                if not sv:
                    is_local = Path(clean_target).exists() and Path(clean_target).is_file()
                    sv = SourceVideo(
                        url=clean_target,
                        title=Path(clean_target).stem if is_local else "Pending Ingestion",
                        duration=float(target_duration),
                        width=1920,
                        height=1080,
                        fps=30.0,
                        format="mp4",
                        local_path=clean_target if is_local else "",
                        source_type="local" if is_local else ("youtube" if "youtube.com" in clean_target else "url"),
                        status="ingesting"
                    )
                    db.add(sv)
                    db.commit()
                    db.refresh(sv)

                job = ProcessingJob(
                    source_video_id=sv.id,
                    job_type="full_pipeline",
                    status="processing",
                    progress_pct=10,
                    current_step=self.STAGES[0],
                    started_at=datetime.utcnow(),
                    last_heartbeat=datetime.utcnow(),
                    worker_pid=os.getpid()
                )
                db.add(job)
                db.commit()
                db.refresh(job)
                job_id = job.id

        self.register_active_job(job_id)
        job_temp_dir = settings.TEMP_DIR / f"job_{job_id}"
        job_temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Stage 1: Source validation
            self._update_job_stage(job_id, 0, self.STAGES[0], 10)
            val_result = ingestion_manager.validate_source(clean_target)
            if not val_result.get("accessible", False):
                raise ValueError(f"Source validation rejected: {val_result.get('message', 'Source not accessible')}")

            # Stage 2: Media ingestion
            self._update_job_stage(job_id, 1, self.STAGES[1], 20)
            self.check_cancellation(job_id)
            source_video = ingestion_manager.process_url_or_path(
                clean_target,
                notes=notes or "",
                job_id=job_id,
                game_name=game_name
            )
            user_game = (game_name.strip() if game_name and game_name.strip() else None) or getattr(source_video, "user_game_name", None)
            if user_game and not getattr(source_video, "user_game_name", None):
                with SessionLocal() as db:
                    sv = db.query(SourceVideo).filter_by(id=source_video.id).first()
                    if sv:
                        sv.user_game_name = user_game
                        db.commit()
                        source_video.user_game_name = user_game

            # STRICT PRE-STAGE 3 VALIDATION GUARD:
            raw_local_path = str(source_video.local_path or "").strip().strip('\"\'')
            clean_path = raw_local_path.replace("\\", "/").lower()
            if (
                not raw_local_path
                or clean_path.startswith(("http://", "https://", "http:/", "https:/", "ftp://", "rtmp://"))
                or "://" in clean_path
                or "youtube.com" in clean_path
                or "youtu.be" in clean_path
            ):
                raise ValueError(
                    f"Media ingestion failed: Ingested source video local_path is an invalid remote URL or empty: '{raw_local_path}'"
                )

            video_path = Path(raw_local_path).resolve()
            if not video_path.exists() or not video_path.is_file():
                raise FileNotFoundError(
                    f"Media ingestion failed: Local video file not found on disk at '{video_path}'."
                )

            if video_path.stat().st_size <= 1024:
                raise ValueError(
                    f"Media ingestion failed: Downloaded video file is empty or corrupted ({video_path.stat().st_size} bytes)."
                )

            # Link real source_video_id to job
            with SessionLocal() as db:
                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                if db_job:
                    db_job.source_video_id = source_video.id
                    db.commit()

            # Stage 3: AI video analysis (Granular, resilient sub-stages)
            self._update_job_stage(job_id, 2, "AI video analysis: audio extraction", 30)
            self.check_cancellation(job_id)
            temp_wav = job_temp_dir / f"audio_{job_id}.wav"
            extract_pcm_wav(video_path, temp_wav, job_id=job_id)

            self._update_job_stage(job_id, 2, "AI video analysis: audio peak detection", 33)
            audio_peaks = calculate_loudness_peaks(temp_wav)

            self._update_job_stage(job_id, 2, "AI video analysis: scene transition detection", 36)
            scene_cuts = detect_scene_cuts(video_path, timeout_seconds=35.0)

            self._update_job_stage(job_id, 2, "AI video analysis: speech signal transcription", 38)
            transcript_text, words_data = transcribe_audio_words(temp_wav, timeout_seconds=35.0)

            # Stage 4: Highlight detection
            self._update_job_stage(job_id, 3, "Highlight detection: analyzing gameplay telemetry", 42)
            highlights_data = gemini_provider.analyze_gameplay_highlights(
                transcript_text=transcript_text,
                scene_timestamps=scene_cuts,
                audio_peaks=audio_peaks,
                video_duration=source_video.duration,
                source_title=source_video.title,
                game_name=user_game
            )

            if not highlights_data:
                raise ValueError("Could not find suitable gameplay moments in source footage.")

            self._update_job_stage(job_id, 3, "Highlight detection: ranking climax events", 46)
            best_hl = highlights_data[0]
            # Enforce target duration for YouTube Shorts (configurable up to 180 seconds / 3 mins)
            target_dur = float(target_duration or settings.DEFAULT_SHORTS_DURATION)
            if target_dur > 180.0 or target_dur <= 0:
                target_dur = float(settings.DEFAULT_SHORTS_DURATION)
            dur = min(target_dur, min(180.0, float(source_video.duration)))
            best_hl["duration_sec"] = dur
            if best_hl["start_sec"] + dur > source_video.duration:
                best_hl["start_sec"] = max(0.0, float(source_video.duration) - dur)
            best_hl["end_sec"] = round(best_hl["start_sec"] + dur, 2)

            with SessionLocal() as db:
                hl_record = DetectedHighlight(
                    source_video_id=source_video.id,
                    start_sec=best_hl["start_sec"],
                    end_sec=best_hl["end_sec"],
                    duration_sec=best_hl["duration_sec"],
                    score=best_hl["score"],
                    event_type=best_hl["event_type"],
                    explanation=best_hl["explanation"],
                    hook_suggestion=best_hl["hook_suggestion"],
                    caption_suggestion=best_hl["caption_suggestion"],
                    confidence=best_hl.get("confidence", 1.0),
                    selected_for_edit=True
                )
                db.add(hl_record)
                db.commit()
                db.refresh(hl_record)
                highlight_id = hl_record.id

            # Stage 5: Clip composition
            self._update_job_stage(job_id, 4, "Clip composition: framing and timing setup", 50)
            ass_path = None
            if enable_captions or enable_watermark:
                # Stage 6: Caption generation
                self._update_job_stage(job_id, 5, "Caption generation: rendering typography", 60)
                ass_path = job_temp_dir / f"subs_{job_id}.ass"
                clip_words = []
                clip_transcript = ""
                if enable_captions:
                    clip_words = [
                        {
                            "word": w["word"],
                            "start": max(0.0, w["start"] - best_hl["start_sec"]),
                            "end": max(0.0, w["end"] - best_hl["start_sec"])
                        }
                        for w in words_data
                        if best_hl["start_sec"] <= w["start"] <= best_hl["end_sec"]
                    ]
                    clip_transcript = " ".join(w["word"] for w in clip_words)

                brand_title = "GAMING UNCUT" if enable_watermark else ""
                brand_tag = "NO FILTER. JUST GAMEPLAY." if enable_watermark else ""

                generate_animated_ass(
                    words_data=clip_words,
                    output_ass_path=ass_path,
                    brand_text=brand_title,
                    tagline=brand_tag,
                    duration_sec=best_hl["duration_sec"]
                )
            else:
                self._update_job_stage(job_id, 5, self.STAGES[5], 60)
                clip_transcript = ""

            # Stage 7: Video rendering
            self._update_job_stage(job_id, 6, "Video rendering: NVENC hardware encoding", 70)
            self.check_cancellation(job_id)
            clip_filename = f"gaming_uncut_{source_video.id}_{highlight_id}_{int(time.time())}.mp4"
            output_clip_path = settings.OUTPUT_DIR / clip_filename

            video_renderer.render_reel(
                input_path=video_path,
                output_path=output_clip_path,
                start_sec=best_hl["start_sec"],
                duration_sec=best_hl["duration_sec"],
                template=template,
                ass_subtitles_path=ass_path,
                job_id=job_id
            )

            self._update_job_stage(job_id, 6, "Video rendering: extracting thumbnail", 80)
            self.check_cancellation(job_id)
            thumb_path = settings.THUMBNAILS_DIR / f"{output_clip_path.stem}_thumb.jpg"
            video_renderer.extract_thumbnail(output_clip_path, thumb_path, job_id=job_id)

            # Stage 8: Quality validation
            self._update_job_stage(job_id, 7, "Quality validation: checking specs and audio", 85)
            # Stage 9: Metadata generation
            self._update_job_stage(job_id, 8, "Metadata generation: SEO titles and tags", 92)
            if enable_ai_metadata:
                ai_metadata = gemini_provider.generate_reel_metadata(
                    gameplay_summary=best_hl["event_type"],
                    highlight_transcript=clip_transcript or transcript_text[:500],
                    clip_duration=best_hl["duration_sec"],
                    video_path=output_clip_path,
                    source_title=source_video.title,
                    user_game_name=user_game
                )
            else:
                ai_metadata = {
                    "title": f"Gameplay Sequence: {source_video.title[:40]}",
                    "topic": user_game or "Gaming Highlights",
                    "description": f"Raw gameplay capture from {source_video.title[:50]} highlighting active gameplay sequences.",
                    "hashtags": "#GamingUncut #Shorts #GameplayMoments #GamingClip",
                    "pinned_comment": "Rate this gameplay sequence from 1 to 10 in the comments below! 👇",
                    "youtube_title": f"{source_video.title[:50]} #Shorts",
                    "youtube_desc": f"Raw gameplay capture from {source_video.title[:50]} highlighting active gameplay sequences.\n\n#GamingUncut #Shorts",
                    "instagram_caption": f"Raw gameplay sequence from {source_video.title[:50]}.\n\n#GamingUncut #reels #GamingClip",
                    "facebook_caption": f"Raw gameplay sequence from {source_video.title[:50]}.\n\n#GamingUncut #Reels",
                    "detected_game": user_game or "Unknown Game",
                    "game_confidence": 0.50 if user_game else 0.0,
                    "user_game_name": user_game,
                    "game_mismatch_flag": False,
                    "analysis_provider": "heuristic",
                    "analysis_status": "incomplete"
                }

            passed, quality_score, quality_report = quality_validator.validate_clip(
                output_clip_path, metadata=ai_metadata
            )

            # Stage 10: Reel ready
            self._update_job_stage(job_id, 9, self.STAGES[9], 100, status="completed")

            with SessionLocal() as db:
                clip_record = GeneratedClip(
                    job_id=job_id,
                    highlight_id=highlight_id,
                    output_path=str(output_clip_path),
                    thumbnail_path=str(thumb_path),
                    duration_sec=best_hl["duration_sec"],
                    width=settings.TARGET_WIDTH,
                    height=settings.TARGET_HEIGHT,
                    layout_template=template,
                    quality_score=quality_score,
                    validation_status="passed" if passed else "flagged"
                )
                db.add(clip_record)
                db.commit()
                db.refresh(clip_record)
                clip_id = clip_record.id

                # Save metadata
                meta_record = ClipMetadata(
                    clip_id=clip_id,
                    title=ai_metadata["title"],
                    topic=ai_metadata["topic"],
                    description=ai_metadata["description"],
                    hashtags=ai_metadata["hashtags"],
                    pinned_comment=ai_metadata.get("pinned_comment", ""),
                    youtube_title=ai_metadata.get("youtube_title", ai_metadata["title"]),
                    youtube_desc=ai_metadata.get("youtube_desc", ai_metadata["description"]),
                    instagram_caption=ai_metadata.get("instagram_caption", ai_metadata["description"]),
                    facebook_caption=ai_metadata.get("facebook_caption", ai_metadata["description"]),
                    detected_game=ai_metadata.get("detected_game", "Unknown Game"),
                    game_confidence=float(ai_metadata.get("game_confidence", 0.0)),
                    game_franchise=ai_metadata.get("game_franchise"),
                    game_genre=ai_metadata.get("game_genre"),
                    user_game_name=ai_metadata.get("user_game_name", user_game),
                    game_mismatch_flag=bool(ai_metadata.get("game_mismatch_flag", False)),
                    gameplay_summary=ai_metadata.get("gameplay_summary"),
                    detected_events_json=json.dumps(ai_metadata.get("detected_events", [])),
                    candidate_titles_json=json.dumps(ai_metadata.get("candidate_titles", [ai_metadata["title"]])),
                    analysis_provider=ai_metadata.get("analysis_provider", "heuristic"),
                    analysis_status=ai_metadata.get("analysis_status", "complete"),
                    analysis_fingerprint=ai_metadata.get("analysis_fingerprint")
                )
                db.add(meta_record)
                db.commit()

                # Generate Platform-Specific SEO & Growth Intelligence Package
                try:
                    from backend.app.metadata.seo_engine import seo_engine
                    seo_engine.generate_clip_seo(
                        clip_id=clip_id,
                        db=db,
                        target_platforms=platforms or ["youtube", "instagram", "facebook"]
                    )
                except Exception as seo_err:
                    logger.warning(f"Automated SEO generation warning for Clip #{clip_id}: {seo_err}")

                # FINAL ACCEPTANCE CRITERIA: A video must not be auto-published if AI analysis is incomplete, heuristic, unconfirmed, OR if user-supplied game name conflicts with visual evidence
                game_mismatch = bool(ai_metadata.get("game_mismatch_flag", False))
                analysis_is_verified = (
                    ai_metadata.get("analysis_provider") == "gemini" and
                    ai_metadata.get("analysis_status") == "complete" and
                    float(ai_metadata.get("game_confidence", 0.0)) >= 0.50 and
                    not game_mismatch
                )
                if publish_mode == "auto" and passed and analysis_is_verified:
                    approval_status = "auto_approved"
                else:
                    approval_status = "pending_review"
                    if publish_mode == "auto":
                        if game_mismatch:
                            logger.warning(
                                f"Clip #{clip_id} auto-publish held for review: User provided game '{user_game}' conflicts with visual analysis ('{ai_metadata.get('detected_game')}'). "
                                f"Discrepancy: {ai_metadata.get('mismatch_reason')}. Held in pending_review."
                            )
                        elif not analysis_is_verified:
                            logger.info(
                                f"Clip #{clip_id} auto-publish held: AI intelligence is "
                                f"{ai_metadata.get('analysis_status', 'unconfirmed')} (provider: {ai_metadata.get('analysis_provider')}). "
                                f"Held in pending_review for manual approval."
                            )
                    if publish_mode == "auto" and not analysis_is_verified:
                        logger.info(
                            f"Clip #{clip_id} auto-publish held: AI intelligence is "
                            f"{ai_metadata.get('analysis_status', 'unconfirmed')} (provider: {ai_metadata.get('analysis_provider')}). "
                            f"Held in pending_review for manual approval."
                        )

                pub_job = PublishingJob(
                    clip_id=clip_id,
                    scheduled_time=datetime.utcnow(),
                    status="pending",
                    approval_status=approval_status
                )
                db.add(pub_job)
                db.flush()

                for plat in platforms:
                    upload_record = PlatformUpload(
                        publishing_job_id=pub_job.id,
                        platform=plat,
                        status="pending"
                    )
                    db.add(upload_record)

                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                if db_job:
                    db_job.completed_at = datetime.utcnow()
                db.commit()
                pub_job_id = pub_job.id

            publish_result = {}
            if approval_status == "auto_approved":
                logger.info(f"Clip #{clip_id} auto-approved. Dispatching publication...")
                try:
                    publish_result = publishing_manager.execute_job(pub_job_id)
                except Exception as e:
                    logger.warning(f"Immediate dispatch notice: {e}")

            return {
                "status": "success",
                "job_id": job_id,
                "clip_id": clip_id,
                "publishing_job_id": pub_job_id,
                "quality_score": quality_score,
                "approval_status": approval_status,
                "video_path": str(output_clip_path),
                "thumbnail_path": str(thumb_path),
                "metadata": ai_metadata,
                "publish_result": publish_result
            }

        except JobCancelledException as e:
            logger.info(f"Pipeline safely aborted for Job #{job_id}: {e}")
            with SessionLocal() as db:
                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                if db_job:
                    db_job.status = "cancelled"
                    db_job.current_step = "Cancelled"
                    db_job.error_message = "Cancelled by user request."
                    db_job.completed_at = datetime.utcnow()
                    db.commit()
            return {
                "status": "cancelled",
                "job_id": job_id,
                "message": f"Job #{job_id} cancelled by user request."
            }

        except Exception as e:
            logger.error(f"Pipeline failure on job #{job_id}: {e}", exc_info=True)
            with SessionLocal() as db:
                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                if db_job:
                    if db_job.cancellation_requested or self.is_cancellation_requested(job_id) or db_job.status in ("cancelling", "cancelled"):
                        db_job.status = "cancelled"
                        db_job.current_step = "Cancelled"
                        db_job.error_message = "Cancelled by user request."
                        db_job.completed_at = datetime.utcnow()
                    else:
                        db_job.status = "failed"
                        db_job.error_message = str(e)
                    db.commit()
                    try:
                        from backend.app.discovery.service import discovery_service
                        discovery_service.sync_pipeline_status(db, job_id, 0, "Error", db_job.status)
                    except Exception:
                        pass
            raise

        finally:
            self.terminate_job_subprocesses(job_id)
            self.unregister_active_job(job_id)
            try:
                if job_temp_dir.exists():
                    shutil.rmtree(job_temp_dir, ignore_errors=True)
            except Exception:
                pass

pipeline_service = PipelineService()
