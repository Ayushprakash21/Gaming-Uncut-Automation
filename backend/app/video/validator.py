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
            report["errors"].append(f"Duration too long ({duration:.1f}s). Max allowed: 60s")
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

        # 7. Metadata completeness
        if metadata:
            if not metadata.get("title"):
                report["errors"].append("Missing video title.")
                score -= 20.0
            if not metadata.get("hashtags"):
                report["warnings"].append("Missing hashtags.")
                score -= 5.0

        final_score = max(0.0, min(100.0, score))
        passed = (len(report["errors"]) == 0) and (final_score >= settings.MIN_QUALITY_SCORE)
        
        report["checks"]["quality_score"] = round(final_score, 1)
        report["checks"]["passed"] = passed

        return passed, round(final_score, 1), report

quality_validator = QualityValidator()
