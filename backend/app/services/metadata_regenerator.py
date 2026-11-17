import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from backend.app.models.schema import GeneratedClip, ClipMetadata, PublishingJob
from backend.app.ai.gemini_provider import gemini_provider
from backend.app.video.validator import quality_validator

logger = logging.getLogger("gaming_uncut.regenerator")

GENERIC_FORBIDDEN_PHRASES = [
    "clutch action climax | gaming uncut",
    "clutch action climax",
    "watch this high-intensity gameplay moment from gaming uncut",
    "watch this high-intensity gameplay moment",
    "#gaminguncut #shorts #gaming #gameplay #clutch #gamer",
    "#gaming #gameplay #clutch #gamer"
]


def is_generic_metadata(title: str, description: str, hashtags: str) -> bool:
    """Checks whether existing metadata matches generic placeholder templates."""
    t_lower = (title or "").lower().strip()
    d_lower = (description or "").lower().strip()
    h_lower = (hashtags or "").lower().strip()

    for phrase in GENERIC_FORBIDDEN_PHRASES:
        if phrase in t_lower or phrase in d_lower or phrase in h_lower:
            return True
    return False


def regenerate_clip_metadata(
    clip_id: int,
    db: Session,
    force_cache_bypass: bool = True
) -> Dict[str, Any]:
    """
    Regenerates grounded, clip-specific metadata for an existing historical clip
    using its actual rendered MP4 video file on disk.
    
    - Never triggers re-upload or automated publishing.
    - Updates database 1-to-1 without overwriting unrelated clips.
    - Flags unconfirmed/heuristic outputs as pending_review.
    """
    clip = db.query(GeneratedClip).filter_by(id=clip_id).first()
    if not clip:
        raise ValueError(f"Clip #{clip_id} not found in database.")

    video_path = Path(clip.output_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Clip #{clip_id} video file does not exist on disk: {video_path}")

    # Extract source context if available
    source_title = ""
    if clip.job and clip.job.source_video:
        source_title = clip.job.source_video.title or ""

    old_meta = clip.metadata_rel
    old_title = old_meta.title if old_meta else ""
    old_desc = old_meta.description if old_meta else ""

    logger.info(f"Regenerating metadata for Clip #{clip_id} ({video_path.name}) from source '{source_title}'")

    # Perform multimodal video understanding using frames and audio
    analysis = gemini_provider.analyze_multimodal_clip(
        video_path=video_path,
        bypass_cache=force_cache_bypass
    )

    titles = analysis.get("content", {}).get("titles", [])
    primary_title = titles[0] if titles else f"Raw Gameplay Clip | Gaming Uncut"
    desc = analysis.get("content", {}).get("description", "")
    hashtags = " ".join(analysis.get("content", {}).get("hashtags", []))

    game_info = analysis.get("game", {})
    detected_game = game_info.get("name", "Unknown Game")
    game_confidence = float(game_info.get("confidence", 0.0))
    game_franchise = game_info.get("franchise", "")
    game_genre = game_info.get("genre", "Action Gameplay")

    gameplay_info = analysis.get("gameplay", {})
    gameplay_summary = gameplay_info.get("summary", "")
    detected_events = gameplay_info.get("events", [])

    analysis_status = analysis.get("analysis_status", "heuristic")
    analysis_provider = analysis.get("analysis_provider", "heuristic")
    fingerprint = analysis.get("analysis_fingerprint", "")

    # Ensure 1-to-1 ClipMetadata mapping
    meta = old_meta
    if not meta:
        meta = ClipMetadata(clip_id=clip_id, title=primary_title)
        db.add(meta)

    meta.title = primary_title[:95]
    meta.topic = game_genre or "Gaming Highlights"
    meta.description = desc
    meta.hashtags = hashtags
    meta.pinned_comment = analysis.get("content", {}).get("pinned_comment", "Rate this play from 1 to 10 in the comments below! 👇")
    meta.youtube_title = f"{primary_title[:80]} #Shorts"
    meta.youtube_desc = f"{desc}\n\n{hashtags}"
    meta.instagram_caption = f"{primary_title}\n\n{desc}\n\n{hashtags}"
    meta.facebook_caption = f"{primary_title}\n\n{desc}\n\n{' '.join(hashtags.split()[:3])}"
    meta.detected_game = detected_game
    meta.game_confidence = round(game_confidence, 2)
    meta.game_franchise = game_franchise
    meta.game_genre = game_genre
    meta.gameplay_summary = gameplay_summary
    meta.detected_events_json = json.dumps(detected_events)
    meta.candidate_titles_json = json.dumps(titles)
    meta.analysis_provider = analysis_provider
    meta.analysis_status = analysis_status
    meta.analysis_fingerprint = fingerprint

    # Safety gate: If analysis is heuristic or uncertain, prevent automatic publishing
    if analysis_status in ("heuristic", "unconfirmed", "AI_ANALYSIS_FAILED", "AI_ANALYSIS_HEURISTIC"):
        for pub_job in clip.publishing_jobs:
            if pub_job.approval_status == "auto_approved":
                pub_job.approval_status = "pending_review"

    db.commit()
    db.refresh(meta)

    # Validate against strict quality rules
    meta_dict = {
        "title": meta.title,
        "description": meta.description,
        "hashtags": meta.hashtags,
        "detected_game": meta.detected_game,
        "game_confidence": meta.game_confidence,
        "analysis_status": meta.analysis_status
    }
    _, quality_score, report = quality_validator.validate_metadata(meta_dict, db=db, exclude_clip_id=clip_id)

    return {
        "clip_id": clip_id,
        "status": "success",
        "video_path": str(video_path),
        "source_title": source_title,
        "old_title": old_title,
        "new_title": meta.title,
        "detected_game": meta.detected_game,
        "game_confidence": meta.game_confidence,
        "analysis_provider": meta.analysis_provider,
        "analysis_status": meta.analysis_status,
        "quality_score": quality_score,
        "validation_warnings": report.get("warnings", []),
        "validation_errors": report.get("errors", []),
        "metadata": {
            "title": meta.title,
            "topic": meta.topic,
            "description": meta.description,
            "hashtags": meta.hashtags,
            "detected_game": meta.detected_game,
            "game_confidence": meta.game_confidence,
            "candidate_titles": titles,
            "analysis_provider": meta.analysis_provider,
            "analysis_status": meta.analysis_status
        }
    }


def batch_regenerate_metadata(
    db: Session,
    clip_ids: Optional[List[int]] = None,
    force_all: bool = False
) -> Dict[str, Any]:
    """
    Batch regenerates metadata for specified clips or all clips flagged with generic templates.
    """
    query = db.query(GeneratedClip).order_by(GeneratedClip.id.asc())
    if clip_ids:
        query = query.filter(GeneratedClip.id.in_(clip_ids))
    all_clips = query.all()

    targets = []
    for c in all_clips:
        if force_all or (clip_ids and c.id in clip_ids):
            targets.append(c)
        else:
            m = c.metadata_rel
            if not m or is_generic_metadata(m.title, m.description, m.hashtags):
                targets.append(c)

    results = []
    errors = []

    for c in targets:
        try:
            res = regenerate_clip_metadata(clip_id=c.id, db=db)
            results.append(res)
        except Exception as e:
            logger.error(f"Failed to regenerate metadata for Clip #{c.id}: {e}")
            errors.append({"clip_id": c.id, "error": str(e)})

    return {
        "total_targets": len(targets),
        "successful_count": len(results),
        "failed_count": len(errors),
        "updated_clips": results,
        "errors": errors
    }
