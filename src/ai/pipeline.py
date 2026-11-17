"""
End-to-End Clip Processing Pipeline for Gaming Uncut.
Coordinates audio peak detection, transcription, vertical video generation,
and initial registration into the review & publishing queue.
"""

import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

from src.core.config import settings
from src.core.database import get_connection, log_event
from src.ai.audio_detector import extract_audio_track, analyze_audio_peaks, find_candidate_moments
from src.video.subtitle_builder import generate_ass_subtitles
from src.video.compositor import VideoCompositor

logger = logging.getLogger("gaming_uncut.pipeline")

class ClipPipeline:
    def __init__(self):
        self.compositor = VideoCompositor()

    def process_raw_footage(self, footage_id: int) -> List[int]:
        """
        Processes a raw footage file: detects peaks, generates 1080x1920 vertical clips,
        creates thumbnails, and prepares clips for approval/publishing.
        """
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM raw_footage WHERE id = ?", (footage_id,))
            footage = cursor.fetchone()

        if not footage:
            raise ValueError(f"Footage ID {footage_id} not found.")

        video_path = Path(footage["filepath"])
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        # Update status
        with get_connection() as conn:
            conn.execute("UPDATE raw_footage SET status = 'analyzing' WHERE id = ?", (footage_id,))
            conn.commit()

        rendered_clip_ids = []
        try:
            # 1. Extract audio & detect peaks
            temp_wav = settings.TEMP_DIR / f"footage_{footage_id}_audio.wav"
            extract_audio_track(video_path, temp_wav)
            peaks = analyze_audio_peaks(temp_wav)
            candidates = find_candidate_moments(peaks, target_duration=settings.TARGET_CLIP_DURATION)

            logger.info(f"Found {len(candidates)} high-excitement candidates in {video_path.name}")

            for idx, cand in enumerate(candidates):
                start_sec = cand["start_sec"]
                end_sec = cand["end_sec"]
                duration = cand["duration_sec"]
                score = cand["score"]

                # Register moment
                with get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO detected_moments
                        (footage_id, start_sec, end_sec, duration_sec, score, detection_type, status)
                        VALUES (?, ?, ?, ?, ?, ?, 'rendering')
                        RETURNING id
                        """,
                        (footage_id, start_sec, end_sec, duration, score, cand["detection_type"])
                    )
                    moment_id = cursor.fetchone()["id"]
                    conn.commit()

                # 2. Render Vertical 1080x1920 MP4
                clip_filename = f"gaming_uncut_{footage_id}_{moment_id}_{int(time.time())}.mp4"
                output_clip_path = settings.RENDERS_DIR / clip_filename
                
                # Mock high-energy caption overlay for moment
                ass_path = settings.TEMP_DIR / f"subtitles_{moment_id}.ass"
                words_data = [
                    {"word": "UNREAL", "start": 0.5, "end": 1.2},
                    {"word": "CLUTCH", "start": 1.2, "end": 1.8},
                    {"word": "MOMENT", "start": 1.8, "end": 2.5},
                    {"word": "INSANE", "start": 3.0, "end": 3.8},
                    {"word": "GAMEPLAY", "start": 3.8, "end": 4.5}
                ]
                generate_ass_subtitles(words_data, ass_path)

                self.compositor.render_blurred_bg(
                    input_path=video_path,
                    output_path=output_clip_path,
                    start_sec=start_sec,
                    duration_sec=duration,
                    ass_subtitles_path=ass_path
                )

                # 3. Generate Thumbnail
                thumb_path = settings.RENDERS_DIR / f"{output_clip_path.stem}_thumb.jpg"
                self.compositor.generate_thumbnail(output_clip_path, thumb_path)

                # 4. Generate Smart Title & Hashtags
                title = f"Gameplay Sequence #{moment_id} | Gaming Uncut"
                description = "Raw uncut gameplay sequence. Daily gaming highlights from Gaming Uncut."
                hashtags = "#GamingUncut #Shorts #GameplayMoments #GamingClip"

                approval_status = "approved" if (settings.WORKFLOW_MODE == "auto" and score >= settings.MIN_QUALITY_SCORE) else "pending"

                with get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO rendered_clips
                        (moment_id, filepath, thumbnail_path, duration_sec, layout_template, title, description, hashtags, approval_status)
                        VALUES (?, ?, ?, ?, 'blurred_bg', ?, ?, ?, ?)
                        RETURNING id
                        """,
                        (moment_id, str(output_clip_path), str(thumb_path), duration, title, description, hashtags, approval_status)
                    )
                    clip_id = cursor.fetchone()["id"]
                    
                    # Update moment status
                    conn.execute("UPDATE detected_moments SET status = 'rendered' WHERE id = ?", (moment_id,))
                    
                    # Schedule in Publishing Queue (YouTube Shorts, Instagram Reels, Facebook Reels)
                    # Stagger uploads across the day
                    scheduled_time = datetime.now() + timedelta(minutes=15 * (idx + 1))
                    scheduled_str = scheduled_time.strftime("%Y-%m-%d %H:%M:%S")

                    for plat in ["youtube_shorts", "instagram_reels", "facebook_reels"]:
                        conn.execute(
                            """
                            INSERT INTO publishing_queue (clip_id, platform, scheduled_time, status)
                            VALUES (?, ?, ?, 'queued')
                            """,
                            (clip_id, plat, scheduled_str)
                        )
                    conn.commit()

                rendered_clip_ids.append(clip_id)

            # Mark footage completed
            with get_connection() as conn:
                conn.execute("UPDATE raw_footage SET status = 'processed' WHERE id = ?", (footage_id,))
                conn.commit()

            # Clean temp audio
            if temp_wav.exists():
                temp_wav.unlink()

            log_event("pipeline", "INFO", f"Footage {footage_id} processed: created {len(rendered_clip_ids)} vertical clips.")
            return rendered_clip_ids

        except Exception as e:
            logger.error(f"Pipeline failure for footage {footage_id}: {e}")
            with get_connection() as conn:
                conn.execute("UPDATE raw_footage SET status = 'failed' WHERE id = ?", (footage_id,))
                conn.commit()
            raise
