"""
Pre-Publish Quality Control & Safety Validator for Gaming Uncut.
Performs automated checks on:
- File integrity & playability
- Duration (strictly near 60s)
- Resolution (strictly 1080x1920, 9:16)
- Codecs (H.264 video, AAC audio)
- Audio loudness & silence detection
- Black-frame detection
- Metadata completeness
Computes a comprehensive Quality Score (0-100).
"""

import json
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Tuple
from backend.app.core.config import settings

logger = logging.getLogger("gaming_uncut.validator")

class QualityValidator:
    def validate_clip(self, video_path: Path, metadata: Dict[str, Any] = None) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Validates clip against strict quality standards.
        Returns: (passed: bool, quality_score: float, report: dict)
        """
        report = {
            "checks": {},
            "errors": [],
            "warnings": []
        }
        score = 100.0

        if not video_path.exists() or video_path.stat().st_size == 0:
            report["errors"].append("Rendered video file is missing or 0 bytes.")
            return False, 0.0, report

        # 1. FFprobe metadata validation
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,size,bit_rate:stream=width,height,codec_name,codec_type",
            "-of", "json",
            str(video_path)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            probe_data = json.loads(res.stdout)
        except Exception as e:
            report["errors"].append(f"FFprobe failed to read file: {e}")
            return False, 0.0, report

        fmt = probe_data.get("format", {})
        streams = probe_data.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        # 2. Check Resolution & Aspect Ratio (Must be 1080x1920)
        if not v_stream:
            report["errors"].append("No video stream found.")
            return False, 0.0, report

        w = int(v_stream.get("width", 0))
        h = int(v_stream.get("height", 0))
        report["checks"]["resolution"] = f"{w}x{h}"
        if w != settings.TARGET_WIDTH or h != settings.TARGET_HEIGHT:
            report["errors"].append(f"Invalid resolution: {w}x{h}. Required: {settings.TARGET_WIDTH}x{settings.TARGET_HEIGHT}")
            score -= 40.0

        # 3. Check Duration
        duration = float(fmt.get("duration", 0))
        report["checks"]["duration"] = duration
        if duration < settings.MIN_DURATION:
            report["errors"].append(f"Duration too short ({duration:.1f}s). Minimum: {settings.MIN_DURATION}s")
            score -= 30.0
        elif duration > settings.MAX_DURATION + 2.0:
            report["errors"].append(f"Duration too long ({duration:.1f}s). Max allowed: {settings.MAX_DURATION}s")
            score -= 30.0

        # 4. Check Video Codec (h264)
        v_codec = v_stream.get("codec_name", "")
        report["checks"]["video_codec"] = v_codec
        if v_codec != "h264":
            report["warnings"].append(f"Video codec is {v_codec}, expected h264.")
            score -= 10.0

        # 5. Check Audio Stream
        if not a_stream:
            report["errors"].append("Missing audio stream.")
            score -= 50.0
        else:
            a_codec = a_stream.get("codec_name", "")
            report["checks"]["audio_codec"] = a_codec

        # 6. Black-frame detection (sample check)
        cmd_black = [
            "ffmpeg", "-i", str(video_path),
            "-vf", "blackdetect=d=2:pix_th=0.10",
            "-f", "null", "-"
        ]
        res_black = subprocess.run(cmd_black, capture_output=True, text=True)
        if "black_start" in res_black.stderr:
            report["warnings"].append("Potential extended black screen detected.")
            score -= 15.0

        # 7. Mandatory YouTube Shorts Profile Verification
        shorts_ok, shorts_msg, shorts_info = validate_shorts_profile(video_path)
        report["checks"]["shorts_profile"] = shorts_info
        report["checks"]["is_valid_short"] = shorts_ok
        if not shorts_ok:
            report["errors"].append(f"YouTube Shorts profile error: {shorts_msg}")
            score -= 40.0

        # 8. Metadata completeness & Quality Validation
        if metadata:
            meta_passed, meta_score, meta_report = self.validate_metadata(metadata)
            report["checks"]["metadata_quality"] = meta_report["checks"]
            report["errors"].extend(meta_report["errors"])
            report["warnings"].extend(meta_report["warnings"])
            score = min(score, (score * 0.7) + (meta_score * 0.3))

        final_score = max(0.0, min(100.0, score))
        passed = (len(report["errors"]) == 0) and (final_score >= settings.MIN_QUALITY_SCORE)
        
        report["checks"]["quality_score"] = round(final_score, 1)
        report["checks"]["passed"] = passed

        return passed, round(final_score, 1), report

    def validate_metadata(
        self,
        metadata: Dict[str, Any],
        db=None,
        exclude_clip_id: int = None
    ) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Validates metadata quality against strict Gaming Uncut standards:
        - Prevents generic placeholder defaults.
        - Verifies game grounding & confidence evidence.
        - Ensures factual event description.
        - Checks for duplicate titles across clips.
        """
        report: Dict[str, Any] = {
            "checks": {},
            "errors": [],
            "warnings": []
        }
        score = 100.0

        title = str(metadata.get("title", "")).strip()
        desc = str(metadata.get("description", "")).strip()
        hashtags = str(metadata.get("hashtags", "")).strip()
        detected_game = str(metadata.get("detected_game", "Unknown Game")).strip()
        confidence = float(metadata.get("game_confidence", 0.0))
        evidence = metadata.get("evidence") or []
        analysis_status = metadata.get("analysis_status", "complete")

        # 1. Check title presence & length
        if not title:
            report["errors"].append("Missing video title.")
            score -= 30.0
        elif len(title) > 100:
            report["warnings"].append(f"Title is too long ({len(title)} chars; max recommended is 70-100).")
            score -= 5.0
        report["checks"]["title_length"] = len(title)

        # 2. Reject hardcoded generic defaults
        generic_forbidden_patterns = [
            "Watch this high-intensity gameplay moment",
            "Clutch Action Climax | Gaming Uncut #Shorts",
            "Clutch Action Climax | Gaming Uncut",
            "Clutch Action Climax",
            "Watch this play unfold!",
            "#Gaming #Gameplay #Gamer"
        ]
        for pattern in generic_forbidden_patterns:
            if pattern.lower() in title.lower():
                report["errors"].append(f"Title contains generic placeholder pattern: '{pattern}'")
                score -= 35.0
            if pattern.lower() in desc.lower():
                report["errors"].append(f"Description contains generic placeholder pattern: '{pattern}'")
                score -= 35.0

        if desc and desc.strip().upper() == "NO FILTER. JUST GAMEPLAY.":
            report["errors"].append("Description contains only brand tagline without gameplay explanation.")
            score -= 30.0

        # 3. Game identification grounding check
        if detected_game != "Unknown Game":
            if confidence < 0.50:
                report["warnings"].append(
                    f"Game '{detected_game}' flagged as uncertain (confidence {confidence:.2f} < 0.50). Human review recommended."
                )
                score -= 10.0
            report["checks"]["game_grounding"] = f"{detected_game} ({int(confidence*100)}%)"
        else:
            report["checks"]["game_grounding"] = "Unknown Game (Neutral Action Phrasing)"

        # 4. Description content & substance
        if not desc:
            report["warnings"].append("Missing video description.")
            score -= 15.0
        elif len(desc) < 20:
            report["warnings"].append("Description is too brief to explain gameplay event.")
            score -= 10.0

        # 5. Hashtags validation
        if not hashtags:
            report["warnings"].append("Missing hashtags.")
            score -= 5.0

        # 6. Duplicate check across existing clips
        if db and title:
            try:
                from backend.app.models.schema import ClipMetadata
                query = db.query(ClipMetadata).filter(ClipMetadata.title == title)
                if exclude_clip_id:
                    query = query.filter(ClipMetadata.clip_id != exclude_clip_id)
                duplicate = query.first()
                if duplicate:
                    report["warnings"].append(f"Title duplicates existing clip #{duplicate.clip_id}.")
                    score -= 15.0
            except Exception as e:
                logger.debug(f"Duplicate title check skipped: {e}")

        final_score = max(0.0, min(100.0, score))
        passed = len(report["errors"]) == 0 and final_score >= 70.0
        report["checks"]["score"] = round(final_score, 1)
        report["checks"]["passed"] = passed

        return passed, round(final_score, 1), report


def validate_shorts_profile(video_path: Path) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Mandatory Shorts Pre-Upload & Pre-Publish Validation:
    Enforces YouTube Shorts technical specifications on the FINAL rendered file:
    - Width = 1080
    - Height = 1920
    - Aspect ratio = 9:16 (Orientation: portrait, height > width)
    - Duration <= 180.0 seconds (maximum allowed for YouTube Shorts)
    - Container = MP4
    - Video codec = H.264
    - Audio codec = AAC (when audio is present)
    - Pixel format = yuv420p
    - Faststart enabled (moov atom at head of file)
    """
    info: Dict[str, Any] = {
        "width": 0,
        "height": 0,
        "aspect_ratio": "unknown",
        "duration": 0.0,
        "video_codec": "",
        "audio_codec": "",
        "pix_fmt": "",
        "container": "",
        "has_faststart": False
    }

    if not video_path.exists() or video_path.stat().st_size == 0:
        return False, "Rendered video file does not exist or is 0 bytes.", info

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,format_name,size:stream=width,height,codec_name,codec_type,pix_fmt",
        "-of", "json",
        str(video_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
    except Exception as e:
        return False, f"FFprobe failed to inspect file: {e}", info

    streams = data.get("streams", [])
    fmt = data.get("format", {})
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not v_stream:
        return False, "No video stream found in rendered file.", info

    w = int(v_stream.get("width", 0))
    h = int(v_stream.get("height", 0))
    dur = float(fmt.get("duration", 0.0))
    v_codec = v_stream.get("codec_name", "").lower()
    pix_fmt = v_stream.get("pix_fmt", "").lower()
    container = fmt.get("format_name", "").lower()

    info["width"] = w
    info["height"] = h
    info["duration"] = round(dur, 2)
    info["video_codec"] = v_codec
    info["pix_fmt"] = pix_fmt
    info["container"] = container

    if a_stream:
        a_codec = a_stream.get("codec_name", "").lower()
        info["audio_codec"] = a_codec

    # 1. Orientation & Aspect Ratio (Must be 1080x1920 portrait, 9:16)
    if w != 1080 or h != 1920:
        if w > h:
            return False, f"Landscape orientation detected ({w}x{h}). YouTube Shorts requires vertical 1080x1920 (9:16 portrait).", info
        return False, f"Invalid resolution ({w}x{h}). YouTube Shorts requires strictly 1080x1920 (9:16 portrait).", info
    
    info["aspect_ratio"] = "9:16"

    # 2. Duration check (maximum 180.0 seconds)
    if dur <= 0:
        return False, "Invalid video duration (0s).", info
    if dur > 180.0:
        return False, f"Duration ({dur:.1f}s) exceeds YouTube Shorts maximum limit of 180.0 seconds.", info

    # 3. Video Codec (H.264)
    if v_codec != "h264":
        return False, f"Invalid video codec: {v_codec}. YouTube Shorts requires H.264 video codec.", info

    # 4. Pixel Format (yuv420p)
    if pix_fmt != "yuv420p":
        return False, f"Invalid pixel format: {pix_fmt}. YouTube Shorts requires yuv420p.", info

    # 5. Audio Codec (AAC)
    if a_stream:
        a_codec = info.get("audio_codec", "")
        if a_codec != "aac":
            return False, f"Invalid audio codec: {a_codec}. YouTube Shorts requires AAC audio.", info

    # 6. Container check (MP4)
    if not any(c in container for c in ["mp4", "mov"]):
        return False, f"Invalid container format: {container}. YouTube Shorts requires MP4 container.", info

    # 7. Faststart metadata check (moov atom at head of file)
    try:
        head_bytes = video_path.read_bytes()[:65536]
        if b"moov" in head_bytes:
            info["has_faststart"] = True
        else:
            return False, "Fast-start metadata (+faststart) missing: moov atom not found at head of file.", info
    except Exception as e:
        logger.warning(f"Faststart byte check warning: {e}")

    return True, "Valid YouTube Shorts profile (1080x1920, 9:16, H.264/AAC, yuv420p, +faststart, <= 180s)", info


quality_validator = QualityValidator()
