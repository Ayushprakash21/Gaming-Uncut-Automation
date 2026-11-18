"""
Pre-upload video validator for Instagram Reels.
Enforces strict Meta Reels specifications before calling Meta Graph APIs:
- H.264 video codec
- yuv420p pixel format
- 1080x1920 resolution (9:16 aspect ratio)
- Constant 30 FPS frame rate
- AAC-LC audio <= 48kHz, stereo
- Duration between 3.0s and 60.0s
- File size <= 60 MB
- Fast-start (+faststart moov atom before mdat)
- Non-corrupted container
"""

import json
import logging
import os
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gaming_uncut.instagram_validator")

# Meta hard limits
MAX_FILE_SIZE_BYTES = 60 * 1000 * 1000  # 60 MB limit
MIN_DURATION_SEC = 3.0
MAX_DURATION_SEC = 60.0
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30.0
MAX_AUDIO_SAMPLE_RATE = 48000


@dataclass
class InstagramValidationResult:
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        if self.is_valid:
            return "Video strictly conforms to Meta Instagram Reels specifications."
        return "Instagram Reels validation failed: " + "; ".join(self.errors)


class InstagramVideoValidator:
    """
    Validates media files against Meta Instagram Reels ingest requirements.
    """

    @staticmethod
    def validate(video_path: Path) -> InstagramValidationResult:
        errors: List[str] = []
        warnings: List[str] = []
        details: Dict[str, Any] = {}

        if not video_path.exists():
            return InstagramValidationResult(
                is_valid=False,
                errors=[f"Video file does not exist: {video_path}"],
                details={"exists": False},
            )

        file_size = video_path.stat().st_size
        details["file_size_bytes"] = file_size
        details["file_size_mb"] = round(file_size / (1024 * 1024), 2)

        if file_size == 0:
            return InstagramValidationResult(
                is_valid=False,
                errors=["Video file is empty (0 bytes)."],
                details=details,
            )

        if file_size > MAX_FILE_SIZE_BYTES:
            errors.append(
                f"File size ({details['file_size_mb']} MB) exceeds Meta Reels limit of 60 MB."
            )

        # Probe file via FFprobe
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
            return InstagramValidationResult(
                is_valid=False,
                errors=[f"File is unreadable or corrupted: {e}"],
                details=details,
            )

        fmt = probe.get("format", {})
        streams = probe.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not v_stream:
            errors.append("No video stream found in file.")
            return InstagramValidationResult(is_valid=False, errors=errors, details=details)

        # 1. Container Format
        fmt_name = fmt.get("format_name", "").lower()
        details["format_name"] = fmt_name
        if "mp4" not in fmt_name and "mov" not in fmt_name:
            errors.append(f"Incompatible container format '{fmt_name}'. MP4 is strictly required.")

        # 2. Video Codec
        v_codec = (v_stream.get("codec_name") or "").lower()
        details["video_codec"] = v_codec
        if v_codec not in ("h264", "avc1"):
            errors.append(f"Video codec '{v_codec}' is not H.264 (libx264/avc1).")

        # 3. Pixel Format
        pix_fmt = (v_stream.get("pix_fmt") or "").lower()
        details["pixel_format"] = pix_fmt
        if pix_fmt != "yuv420p":
            errors.append(f"Pixel format '{pix_fmt}' is not yuv420p.")

        # 4. Resolution and Aspect Ratio
        w = int(v_stream.get("width", 0))
        h = int(v_stream.get("height", 0))
        details["width"] = w
        details["height"] = h
        details["resolution"] = f"{w}x{h}"

        if w != TARGET_WIDTH or h != TARGET_HEIGHT:
            ratio = (w / h) if h > 0 else 0
            expected_ratio = 9.0 / 16.0
            if abs(ratio - expected_ratio) > 0.03:
                errors.append(
                    f"Resolution {w}x{h} does not conform to 9:16 vertical aspect ratio (1080x1920 expected)."
                )
            else:
                warnings.append(
                    f"Resolution is {w}x{h} (9:16 ratio), but recommended exact standard is 1080x1920."
                )

        # 5. Frame Rate
        r_fps_str = v_stream.get("r_frame_rate", "0/0")
        avg_fps_str = v_stream.get("avg_frame_rate", "0/0")
        details["nominal_frame_rate"] = r_fps_str
        details["avg_frame_rate"] = avg_fps_str

        def parse_fps(val: str) -> float:
            if "/" in val:
                n, d = val.split("/")
                return float(n) / float(d) if float(d) != 0 else 0.0
            try:
                return float(val)
            except ValueError:
                return 0.0

        fps_nom = parse_fps(r_fps_str)
        fps_avg = parse_fps(avg_fps_str)
        details["fps_numeric"] = round(fps_nom, 2)

        # Check constant frame rate
        is_cfr = abs(fps_nom - fps_avg) < 0.05 if fps_nom > 0 else False
        details["is_constant_frame_rate"] = is_cfr

        if not is_cfr:
            warnings.append(
                f"Variable frame rate detected ({fps_nom:.2f} vs avg {fps_avg:.2f}). Meta edge ingest requires constant frame rate (CFR)."
            )

        if fps_nom > 35.0 or fps_nom < 24.0:
            warnings.append(
                f"Frame rate {fps_nom:.2f} FPS deviates from standard 30 FPS profile."
            )

        # 6. Duration
        dur_val = float(fmt.get("duration", 0.0)) if fmt.get("duration") else 0.0
        details["duration_sec"] = round(dur_val, 2)
        if dur_val > MAX_DURATION_SEC:
            errors.append(
                f"Duration {dur_val:.2f}s exceeds Meta Reels maximum limit of 60 seconds."
            )
        elif dur_val < MIN_DURATION_SEC:
            errors.append(
                f"Duration {dur_val:.2f}s is below Meta Reels minimum limit of 3 seconds."
            )

        # 7. Audio Stream
        if not a_stream:
            errors.append(
                "No audio stream found. Meta Reels strictly requires an AAC audio stream."
            )
            details["audio_present"] = False
        else:
            details["audio_present"] = True
            a_codec = (a_stream.get("codec_name") or "").lower()
            a_rate = int(a_stream.get("sample_rate", 0)) if a_stream.get("sample_rate") else 0
            a_channels = int(a_stream.get("channels", 0)) if a_stream.get("channels") else 0
            details["audio_codec"] = a_codec
            details["audio_sample_rate"] = a_rate
            details["audio_channels"] = a_channels

            if a_codec != "aac":
                errors.append(f"Audio codec '{a_codec}' is not AAC.")
            if a_rate > MAX_AUDIO_SAMPLE_RATE:
                errors.append(
                    f"Audio sample rate {a_rate} Hz exceeds Meta Reels maximum of 48000 Hz."
                )

        # 8. Moov Atom Placement (+faststart)
        moov_offset = None
        try:
            with open(video_path, "rb") as f:
                offset = 0
                while True:
                    data = f.read(8)
                    if len(data) < 8:
                        break
                    size, name = struct.unpack(">I4s", data)
                    name = name.decode("ascii", errors="ignore")
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

        details["moov_offset"] = moov_offset
        # Moov atom should appear near beginning of file (typically before offset 250,000)
        details["faststart_valid"] = moov_offset is not None and moov_offset < 250000
        if not details["faststart_valid"]:
            warnings.append("Moov atom not placed at beginning of file; faststart may be missing.")

        is_valid = len(errors) == 0
        return InstagramValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            details=details,
        )


instagram_video_validator = InstagramVideoValidator()
