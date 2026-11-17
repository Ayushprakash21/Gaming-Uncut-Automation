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
    def execute_from_url_or_path(
        self,
        url_or_path: str,
        template: str = "blurred_bg",
        publish_mode: str = "auto",
        platforms: list[str] = None
    ) -> Dict[str, Any]:
        """
        Executes full pipeline:
        1. Ingests source.
        2. Detects climax moments with audio/scene analysis + Gemini AI.
        3. Renders 60-second 1080x1920 vertical video with NVENC.
        4. Burns word-level subtitles & Gaming Uncut branding.
        5. Generates optimized title/hashtags/captions.
        6. Enqueues and publishes if quality checks pass.
        """
        if platforms is None:
            platforms = ["youtube", "instagram", "facebook"]

        # Step 1: Ingestion
        source_video = ingestion_manager.process_url_or_path(url_or_path)
        video_path = Path(source_video.local_path)

        with SessionLocal() as db:
            job = ProcessingJob(
                source_video_id=source_video.id,
                job_type="full_pipeline",
                status="analyzing",
                progress_pct=10,
                current_step="Analyzing Audio Signals & Scene Cuts",
                started_at=datetime.utcnow()
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            job_id = job.id

        try:
            # Step 2: Signal Extraction
            temp_wav = settings.TEMP_DIR / f"job_{job_id}_audio.wav"
            extract_pcm_wav(video_path, temp_wav)
            audio_peaks = calculate_loudness_peaks(temp_wav)
            scene_cuts = detect_scene_cuts(video_path)
            transcript_text, words_data = transcribe_audio_words(temp_wav)

            # Step 3: AI Highlight Selection
            with SessionLocal() as db:
                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                db_job.progress_pct = 35
                db_job.current_step = "AI Highlight Scoring & Selection"
                db.commit()

            highlights_data = gemini_provider.analyze_gameplay_highlights(
                transcript_text=transcript_text,
                scene_timestamps=scene_cuts,
                audio_peaks=audio_peaks,
                video_duration=source_video.duration
            )

            if not highlights_data:
                raise ValueError("Could not find any suitable gameplay moments in source footage.")

            # Best highlight
            best_hl = highlights_data[0]

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

            # Step 4: Subtitles & Video Rendering
            with SessionLocal() as db:
                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                db_job.progress_pct = 55
                db_job.current_step = f"Rendering 1080x1920 Video with NVENC ({template})"
                db.commit()

            ass_path = settings.TEMP_DIR / f"job_{job_id}_subs.ass"
            # Filter words for the selected highlight window
            clip_words = [
                {
                    "word": w["word"],
                    "start": max(0.0, w["start"] - best_hl["start_sec"]),
                    "end": max(0.0, w["end"] - best_hl["start_sec"])
                }
                for w in words_data
                if best_hl["start_sec"] <= w["start"] <= best_hl["end_sec"]
            ]
            
            # If no speech in clip, provide punchy action hooks
            if not clip_words:
                clip_words = [
                    {"word": "NO", "start": 0.5, "end": 1.2},
                    {"word": "FILTER.", "start": 1.2, "end": 2.0},
                    {"word": "JUST", "start": 2.0, "end": 2.8},
                    {"word": "GAMEPLAY.", "start": 2.8, "end": 3.8}
                ]

            generate_animated_ass(
                words_data=clip_words,
                output_ass_path=ass_path,
                brand_text="GAMING UNCUT",
                tagline="NO FILTER. JUST GAMEPLAY.",
                duration_sec=best_hl["duration_sec"]
            )

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

            # Extract thumbnail
            thumb_path = settings.THUMBNAILS_DIR / f"{output_clip_path.stem}_thumb.jpg"
            video_renderer.extract_thumbnail(output_clip_path, thumb_path)

            # Step 5: Quality Validation & AI Metadata
            with SessionLocal() as db:
                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                db_job.progress_pct = 80
                db_job.current_step = "Quality Validation & Metadata Generation"
                db.commit()

            ai_metadata = gemini_provider.generate_reel_metadata(
                gameplay_summary=best_hl["event_type"],
                highlight_transcript=transcript_text,
                clip_duration=best_hl["duration_sec"]
            )

            passed, quality_score, quality_report = quality_validator.validate_clip(
                output_clip_path, metadata=ai_metadata
            )

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
                    pinned_comment=ai_metadata["pinned_comment"],
                    youtube_title=ai_metadata["youtube_title"],
                    youtube_desc=ai_metadata["youtube_desc"],
                    instagram_caption=ai_metadata["instagram_caption"],
                    facebook_caption=ai_metadata["facebook_caption"]
                )
                db.add(meta_record)

                # Step 6: Create Publishing Job
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

                # Mark processing job completed
                db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                db_job.progress_pct = 100
                db_job.status = "completed"
                db_job.current_step = "Finished"
                db_job.completed_at = datetime.utcnow()
                db.commit()
                pub_job_id = pub_job.id

            # Clean temporary wav
            if temp_wav.exists():
                temp_wav.unlink()

            # Step 7: Auto-Dispatch if auto-approved
            publish_result = {}
            if approval_status == "auto_approved":
                logger.info(f"Clip #{clip_id} auto-approved (Quality Score {quality_score}/100). Dispatching publication...")
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
