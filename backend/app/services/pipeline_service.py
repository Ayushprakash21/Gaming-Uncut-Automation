"""
End-to-End Autonomous Pipeline Service for Gaming Uncut Automation.
Coordinates Ingestion -> Analysis -> Highlight Selection -> 60s Video Rendering ->
Quality Control -> AI Metadata -> Scheduled Multi-Platform Publishing.
"""

import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    SourceVideo, ProcessingJob, DetectedHighlight, GeneratedClip, ClipMetadata,
    PublishingJob, PlatformUpload, SystemLog, ApplicationEvent
)
from backend.app.ingestion.adapters import ingestion_manager
from backend.app.ai.audio_analyzer import extract_pcm_wav, calculate_loudness_peaks
from backend.app.ai.scene_analyzer import detect_scene_cuts
from backend.app.ai.transcriber import transcribe_audio_words
from backend.app.ai.gemini_provider import gemini_provider
from backend.app.captions.builder import generate_animated_ass
from backend.app.video.renderer import video_renderer
from backend.app.video.validator import quality_validator
from backend.app.publishers.manager import publishing_manager

logger = logging.getLogger("gaming_uncut.pipeline")

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

    def _update_job_stage(self, job_id: int, stage_idx: int, stage_name: str, progress_pct: int, status: str = "processing"):
        with SessionLocal() as db:
            db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
            if db_job:
                db_job.status = status
                db_job.progress_pct = progress_pct
                db_job.current_step = stage_name
                db.commit()

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
        notes: Optional[str] = None
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

        # Ensure initial Job record exists
        if job_id is None:
            with SessionLocal() as db:
                clean_target = url_or_path.strip()
                sv = db.query(SourceVideo).filter(
                    (SourceVideo.url == clean_target) | (SourceVideo.local_path == clean_target)
                ).first()
                if not sv:
                    sv = SourceVideo(
                        url=clean_target,
                        title=Path(clean_target).stem,
                        duration=float(target_duration),
                        width=1920,
                        height=1080,
                        fps=30.0,
                        format="mp4",
                        local_path=clean_target,
                        source_type="local" if Path(clean_target).exists() else "url",
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
                    started_at=datetime.utcnow()
                )
                db.add(job)
                db.commit()
                db.refresh(job)
                job_id = job.id

        try:
            # Stage 1: Source validation
            self._update_job_stage(job_id, 0, self.STAGES[0], 10)
            val_result = ingestion_manager.validate_source(url_or_path)
            if not val_result.get("accessible", False):
                raise ValueError(f"Source validation rejected: {val_result.get('message', 'Source not accessible')}")

            # Stage 2: Media ingestion
            self._update_job_stage(job_id, 1, self.STAGES[1], 20)
            source_video = ingestion_manager.process_url_or_path(url_or_path, notes=notes or "")
            video_path = Path(source_video.local_path)

            # Link real source_video_id to job
            with SessionLocal() as db:
                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                if db_job:
                    db_job.source_video_id = source_video.id
                    db.commit()

            # Stage 3: AI video analysis
            self._update_job_stage(job_id, 2, self.STAGES[2], 30)
            temp_wav = settings.TEMP_DIR / f"job_{job_id}_audio.wav"
            extract_pcm_wav(video_path, temp_wav)
            audio_peaks = calculate_loudness_peaks(temp_wav)
            scene_cuts = detect_scene_cuts(video_path)
            transcript_text, words_data = transcribe_audio_words(temp_wav)

            # Stage 4: Highlight detection
            self._update_job_stage(job_id, 3, self.STAGES[3], 40)
            highlights_data = gemini_provider.analyze_gameplay_highlights(
                transcript_text=transcript_text,
                scene_timestamps=scene_cuts,
                audio_peaks=audio_peaks,
                video_duration=source_video.duration
            )

            if not highlights_data:
                raise ValueError("Could not find suitable gameplay moments in source footage.")

            best_hl = highlights_data[0]
            # Enforce target duration
            dur = min(float(target_duration), source_video.duration)
            if abs(best_hl["duration_sec"] - dur) > 5.0 and source_video.duration >= dur:
                best_hl["duration_sec"] = dur
                best_hl["end_sec"] = min(source_video.duration, best_hl["start_sec"] + dur)

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
            self._update_job_stage(job_id, 4, self.STAGES[4], 50)
            ass_path = None
            if enable_captions or enable_watermark:
                # Stage 6: Caption generation
                self._update_job_stage(job_id, 5, self.STAGES[5], 60)
                ass_path = settings.TEMP_DIR / f"job_{job_id}_subs.ass"
                clip_words = []
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
                    if not clip_words:
                        clip_words = [
                            {"word": "NO", "start": 0.5, "end": 1.2},
                            {"word": "FILTER.", "start": 1.2, "end": 2.0},
                            {"word": "JUST", "start": 2.0, "end": 2.8},
                            {"word": "GAMEPLAY.", "start": 2.8, "end": 3.8}
                        ]

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

            # Stage 7: Video rendering
            self._update_job_stage(job_id, 6, self.STAGES[6], 75)
            clip_filename = f"gaming_uncut_{source_video.id}_{highlight_id}_{int(time.time())}.mp4"
            output_clip_path = settings.OUTPUT_DIR / clip_filename

            video_renderer.render_reel(
                input_path=video_path,
                output_path=output_clip_path,
                start_sec=best_hl["start_sec"],
                duration_sec=best_hl["duration_sec"],
                template=template,
                ass_subtitles_path=ass_path
            )

            thumb_path = settings.THUMBNAILS_DIR / f"{output_clip_path.stem}_thumb.jpg"
            video_renderer.extract_thumbnail(output_clip_path, thumb_path)

            # Stage 8: Quality validation
            self._update_job_stage(job_id, 7, self.STAGES[7], 85)
            # Stage 9: Metadata generation
            self._update_job_stage(job_id, 8, self.STAGES[8], 95)
            if enable_ai_metadata:
                ai_metadata = gemini_provider.generate_reel_metadata(
                    gameplay_summary=best_hl["event_type"],
                    highlight_transcript=transcript_text,
                    clip_duration=best_hl["duration_sec"]
                )
            else:
                ai_metadata = {
                    "title": f"Gaming Uncut Highlight: {source_video.title[:40]}",
                    "topic": "Gaming",
                    "description": "Pure unadulterated gameplay moments. No commentary, no filter. #GamingUncut",
                    "hashtags": "#gaming #shorts #gaminguncut #gameplay #viral",
                    "pinned_comment": "Subscribe to Gaming Uncut for daily highlights!",
                    "youtube_title": f"{source_video.title[:50]} #Shorts",
                    "youtube_desc": "Pure unadulterated gameplay moments. #GamingUncut",
                    "instagram_caption": "Pure gameplay moments. #GamingUncut #reels",
                    "facebook_caption": "Pure gameplay moments. #GamingUncut"
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
                    facebook_caption=ai_metadata.get("facebook_caption", ai_metadata["description"])
                )
                db.add(meta_record)

                approval_status = "auto_approved" if (publish_mode == "auto" and passed) else "pending_review"
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

            # Clean temporary wav
            if temp_wav.exists():
                try:
                    temp_wav.unlink()
                except Exception:
                    pass

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

        except Exception as e:
            logger.error(f"Pipeline failure on job #{job_id}: {e}", exc_info=True)
            with SessionLocal() as db:
                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                if db_job:
                    db_job.status = "failed"
                    db_job.error_message = str(e)
                    db.commit()
            raise

pipeline_service = PipelineService()
