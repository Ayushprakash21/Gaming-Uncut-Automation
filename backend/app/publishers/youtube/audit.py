"""
YouTube Shorts Classification & Compliance Audit Module.
Inspects existing and pending YouTube upload records and their rendered MP4 files
to verify compliance with the mandatory YouTube Shorts profile:
1. Resolution: 1080x1920
2. Aspect ratio: 9:16 (Orientation: portrait)
3. Duration: <= 180.0 seconds
4. Codecs: H.264 video, AAC audio
5. Pixel format: yuv420p
6. Fast-start metadata enabled (+faststart)
"""

import subprocess
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

from backend.app.database.session import SessionLocal
from backend.app.models.schema import PlatformUpload
from backend.app.video.validator import validate_shorts_profile

logger = logging.getLogger("gaming_uncut.youtube_audit")

def audit_youtube_uploads() -> Dict[str, Any]:
    """
    Scans all database PlatformUpload records for platform='youtube'.
    Inspects the underlying clip files and classifies compliance.
    """
    results: List[Dict[str, Any]] = []
    compliant_count = 0
    non_compliant_count = 0
    missing_files_count = 0

    with SessionLocal() as db:
        uploads = db.query(PlatformUpload).filter_by(platform="youtube").all()
        for u in uploads:
            job = u.publishing_job
            clip = job.clip if job else None
            output_path = Path(clip.output_path) if clip and clip.output_path else None

            entry: Dict[str, Any] = {
                "upload_id": u.id,
                "publishing_job_id": u.publishing_job_id,
                "status": u.status,
                "post_id": u.external_post_id,
                "url": u.external_url,
                "file_path": str(output_path) if output_path else None,
                "exists": output_path.exists() if output_path else False,
                "is_shorts_compliant": False,
                "violations": [],
                "dimensions": None,
                "aspect_ratio": None,
                "duration_sec": 0.0,
                "video_codec": None,
                "audio_codec": None
            }

            if not output_path or not output_path.exists():
                entry["violations"].append("Output video file missing on disk")
                missing_files_count += 1
                non_compliant_count += 1
                results.append(entry)
                continue

            # Run pre-upload shorts validation
            is_valid, reason, info = validate_shorts_profile(output_path)
            entry["is_shorts_compliant"] = is_valid
            entry["dimensions"] = f"{info['width']}x{info['height']}"
            entry["aspect_ratio"] = info["aspect_ratio"]
            entry["duration_sec"] = info["duration"]
            entry["video_codec"] = info["video_codec"]
            entry["audio_codec"] = info.get("audio_codec")

            if is_valid:
                compliant_count += 1
            else:
                entry["violations"].append(reason)
                non_compliant_count += 1

            results.append(entry)

    report = {
        "total_audited": len(results),
        "compliant_shorts": compliant_count,
        "non_compliant": non_compliant_count,
        "missing_files": missing_files_count,
        "records": results
    }
    return report

if __name__ == "__main__":
    rep = audit_youtube_uploads()
    print(json.dumps(rep, indent=2))
