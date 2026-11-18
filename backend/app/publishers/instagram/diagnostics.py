"""
Diagnostic inspection tool for Instagram Reels media assets.
Captures container format, video codec, resolution, frame rate, pixel format,
audio codec, duration, file size, bitrate, and moov atom placement.
Records results in the database audit log without exposing credentials.
"""

import json
import logging
import os
import struct
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.app.database.session import SessionLocal
from backend.app.models.schema import SystemLog

logger = logging.getLogger("gaming_uncut.instagram_diagnostics")


def inspect_video_for_instagram(video_path: Path) -> Dict[str, Any]:
    """
    Performs full FFprobe and MP4 atom inspection on a video file.
    Returns comprehensive diagnostic metrics and violations against Meta Reels specifications.
    """
    if not video_path.exists():
        return {
            "exists": False,
            "error": f"File not found: {video_path}",
            "meta_compliance_violations": ["File does not exist"],
        }

    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        probe = json.loads(res.stdout)
    except Exception as e:
        return {
            "exists": True,
            "error": f"FFprobe execution failed: {e}",
            "meta_compliance_violations": ["File is unreadable or corrupted"],
        }

    fmt = probe.get("format", {})
    streams = probe.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    # Check moov atom placement in MP4
    moov_offset = None
    first_atom = None
    try:
        with open(video_path, "rb") as f:
            offset = 0
            while True:
                data = f.read(8)
                if len(data) < 8:
                    break
                size, name = struct.unpack(">I4s", data)
                name = name.decode("ascii", errors="ignore")
                if first_atom is None and name != "ftyp":
                    first_atom = name
                if name == "moov":
                    moov_offset = offset
                    break
                if size == 1:
                    data64 = f.read(8)
                    size = struct.unpack(">Q", data64)[0]
                    f.seek(size - 16, 1)
                    offset += size
                elif size == 0:
                    break
                else:
                    f.seek(size - 8, 1)
                    offset += size
    except Exception:
        moov_offset = None

    file_size_bytes = video_path.stat().st_size
    file_size_mb = round(file_size_bytes / (1024 * 1024), 2)
    bitrate = int(fmt.get("bit_rate", 0)) if fmt.get("bit_rate") else 0
    duration_sec = float(fmt.get("duration", 0.0)) if fmt.get("duration") else 0.0

    width = int(v_stream.get("width", 0))
    height = int(v_stream.get("height", 0))
    r_fps = v_stream.get("r_frame_rate", "0/0")
    avg_fps = v_stream.get("avg_frame_rate", "0/0")

    # Evaluate FPS value
    nominal_fps = 0.0
    if "/" in r_fps:
        num, den = r_fps.split("/")
        nominal_fps = float(num) / float(den) if float(den) != 0 else 0.0

    avg_fps_val = 0.0
    if "/" in avg_fps:
        num, den = avg_fps.split("/")
        avg_fps_val = float(num) / float(den) if float(den) != 0 else 0.0

    is_cfr = abs(nominal_fps - avg_fps_val) < 0.05 if nominal_fps > 0 else False

    audio_codec = a_stream.get("codec_name") if a_stream else None
    audio_sample_rate = int(a_stream.get("sample_rate", 0)) if a_stream and a_stream.get("sample_rate") else None
    audio_channels = int(a_stream.get("channels", 0)) if a_stream and a_stream.get("channels") else 0

    violations = []
    # 1. File size
    if file_size_bytes > 60 * 1000 * 1000:
        violations.append(
            f"File size {file_size_mb} MB ({file_size_bytes} bytes) exceeds Meta Reels limit of 60 MB"
        )

    # 2. Duration
    if duration_sec > 60.0:
        violations.append(f"Duration {duration_sec:.2f}s exceeds Meta Reels maximum of 60 seconds")
    elif duration_sec < 3.0:
        violations.append(f"Duration {duration_sec:.2f}s is below Meta Reels minimum of 3 seconds")

    # 3. Video Codec
    v_codec = (v_stream.get("codec_name") or "").lower()
    if v_codec not in ("h264", "avc1"):
        violations.append(f"Video codec '{v_codec}' is not supported; H.264 is strictly required")

    # 4. Pixel Format
    pix_fmt = v_stream.get("pix_fmt") or ""
    if pix_fmt != "yuv420p":
        violations.append(f"Pixel format '{pix_fmt}' is incompatible; yuv420p is strictly required")

    # 5. Aspect ratio & Resolution
    if width != 1080 or height != 1920:
        # Check aspect ratio
        ratio = (width / height) if height > 0 else 0
        if abs(ratio - (9 / 16)) > 0.02:
            violations.append(f"Resolution {width}x{height} is not 9:16 vertical portrait (1080x1920)")

    # 6. Frame Rate (Meta Reels recommends 30 FPS CFR)
    if nominal_fps > 35.0 or nominal_fps < 24.0:
        violations.append(f"Nominal frame rate {nominal_fps:.2f} FPS deviates from 30 FPS profile")
    if not is_cfr:
        violations.append(f"Frame rate appears variable ({r_fps} vs avg {avg_fps}); CFR required")

    # 7. Audio stream
    if not a_stream:
        violations.append("Missing audio stream; Meta Reels requires AAC-LC audio")
    else:
        if audio_codec != "aac":
            violations.append(f"Audio codec '{audio_codec}' is not AAC; AAC-LC is strictly required")
        if audio_sample_rate and audio_sample_rate > 48000:
            violations.append(f"Audio sample rate {audio_sample_rate} Hz exceeds 48000 Hz limit")

    # 8. Fast-start
    faststart = moov_offset is not None and moov_offset < 200000
    if not faststart:
        violations.append("MP4 moov atom is located after mdat; fast-start flag (+faststart) missing")

    diag = {
        "container_format": fmt.get("format_name"),
        "video_codec": v_codec,
        "video_profile": v_stream.get("profile"),
        "resolution": f"{width}x{height}",
        "aspect_ratio": f"{width}:{height}" if height > 0 else "unknown",
        "avg_frame_rate": avg_fps,
        "nominal_frame_rate": r_fps,
        "nominal_fps_numeric": round(nominal_fps, 2),
        "is_constant_frame_rate": is_cfr,
        "pixel_format": pix_fmt,
        "audio_codec": audio_codec,
        "audio_sample_rate": audio_sample_rate,
        "audio_channels": audio_channels,
        "duration_sec": round(duration_sec, 2),
        "file_size_bytes": file_size_bytes,
        "file_size_mb": file_size_mb,
        "bitrate_bps": bitrate,
        "moov_atom_offset": moov_offset,
        "faststart_enabled": faststart,
        "meta_compliance_violations": violations,
        "is_compliant": len(violations) == 0,
    }

    return diag


def log_video_diagnostic_record(job_id: int, clip_id: int, diag: Dict[str, Any]):
    """
    Persists diagnostic details into the SystemLog table for audit purposes.
    """
    safe_diag = {k: v for k, v in diag.items() if not str(k).lower().endswith("token")}
    msg = (
        f"[Instagram Diagnostic] Job #{job_id} (Clip #{clip_id}) Video Specs: "
        f"Codec={safe_diag.get('video_codec')} | Res={safe_diag.get('resolution')} | "
        f"FPS={safe_diag.get('nominal_fps_numeric')} ({'CFR' if safe_diag.get('is_constant_frame_rate') else 'VFR'}) | "
        f"PixFmt={safe_diag.get('pixel_format')} | Audio={safe_diag.get('audio_codec')}@{safe_diag.get('audio_sample_rate')}Hz | "
        f"Duration={safe_diag.get('duration_sec')}s | Size={safe_diag.get('file_size_mb')}MB | "
        f"Compliant={safe_diag.get('is_compliant')} | "
        f"Violations={safe_diag.get('meta_compliance_violations')}"
    )

    with SessionLocal() as db:
        log = SystemLog(
            level="WARNING" if not safe_diag.get("is_compliant") else "INFO",
            module="instagram_diagnostics",
            message=msg,
            created_at=datetime.utcnow(),
        )
        db.add(log)
        db.commit()
    logger.info(msg)
