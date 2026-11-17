"""
Multimodal Frame Extraction Pipeline for Gaming Uncut Automation.
Extracts adaptive representative video frames around key gameplay moments and audio spikes
using FFmpeg for ingestion into Gemini Multimodal Vision API.
"""

import subprocess
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from backend.app.core.config import settings

logger = logging.getLogger("gaming_uncut.frame_extractor")


def get_video_duration(video_path: Path) -> float:
    """Retrieves video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(res.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to probe duration for {video_path}: {e}")
        return 60.0


def extract_multimodal_frames(
    video_path: Path,
    output_dir: Optional[Path] = None,
    peak_timestamps: Optional[List[float]] = None,
    max_frames: int = 6
) -> List[Dict[str, Any]]:
    """
    Extracts high-quality JPEG frames from key moments of the video:
    - Opening frame (t = 1.0s)
    - Peak action timestamps (around audio spikes or scene cuts)
    - Representative midpoint frames
    - Climax / final frame (t = duration - 1.5s)
    
    Returns a list of frame dictionaries:
    [
      {
        "timestamp": float,
        "path": Path,
        "role": str,  # 'opening', 'action_peak', 'midpoint', 'climax'
        "bytes": bytes
      }
    ]
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")

    duration = get_video_duration(video_path)
    if duration <= 1.0:
        duration = 5.0

    if output_dir is None:
        output_dir = settings.TEMP_DIR / "analysis_frames" / video_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Determine target timestamps adaptively
    targets: List[Dict[str, Any]] = []

    # Opening frame
    t_open = min(1.0, duration * 0.1)
    targets.append({"ts": round(t_open, 2), "role": "opening"})

    # Peak timestamps (if provided)
    if peak_timestamps:
        for pt in peak_timestamps:
            if 1.0 < pt < (duration - 1.5):
                targets.append({"ts": round(pt, 2), "role": "action_peak"})
                if len(targets) >= max_frames - 2:
                    break

    # If we still have slots, sample evenly across timeline
    if len(targets) < max_frames - 1:
        needed = max_frames - 1 - len(targets)
        step = duration / (needed + 2)
        for i in range(1, needed + 1):
            ts = round(step * i, 2)
            if not any(abs(t["ts"] - ts) < 2.0 for t in targets):
                targets.append({"ts": ts, "role": "midpoint"})

    # Final climax / resolution frame
    t_final = max(t_open + 1.0, duration - 1.5)
    targets.append({"ts": round(t_final, 2), "role": "climax"})

    # Sort and take top max_frames
    targets = sorted(targets, key=lambda x: x["ts"])[:max_frames]

    extracted_frames: List[Dict[str, Any]] = []

    for idx, target in enumerate(targets):
        ts = target["ts"]
        role = target["role"]
        frame_file = output_dir / f"frame_{idx:02d}_{role}_{int(ts*1000)}ms.jpg"

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(ts),
            "-i", str(video_path),
            "-vframes", "1",
            "-vf", "scale='min(1280,iw)':-2",
            "-q:v", "3",
            str(frame_file)
        ]

        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            if frame_file.exists() and frame_file.stat().st_size > 0:
                frame_bytes = frame_file.read_bytes()
                extracted_frames.append({
                    "timestamp": ts,
                    "path": frame_file,
                    "role": role,
                    "bytes": frame_bytes
                })
        except Exception as e:
            logger.warning(f"Error extracting frame at {ts}s: {e}")

    logger.info(f"Extracted {len(extracted_frames)} multimodal analysis frames from {video_path.name}")
    return extracted_frames
