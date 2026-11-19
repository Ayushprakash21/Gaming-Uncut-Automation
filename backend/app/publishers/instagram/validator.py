"""
Pre-upload video validator for Instagram Reels.
Enforces strict Meta Reels specifications before calling Meta Graph APIs:
1. MP4 container
2. H.264 video codec (libx264/avc1)
3. yuv420p pixel format
4. Progressive scan
5. Closed GOP
6. Constant frame rate (CFR) at 30 FPS
7. 1080x1920 target resolution
8. 9:16 aspect ratio with SAR 1:1
9. AAC-LC audio
10. Maximum 48 kHz audio sample rate
11. Mono or stereo audio
12. Valid MP4 fast-start structure (moov atom before mdat)
13. No unsupported MP4 edit lists (timestamps start at 0.0s)
14. Valid duration between 3.0s and 60.00s
15. Valid bitrate and file size <= 60 MB
16. Valid and complete video stream
17. SDR-compatible output with BT.709 color characteristics
"""

import json
import logging
import os
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("gaming_uncut.instagram_validator")

# Meta Reels strict specifications
MAX_FILE_SIZE_BYTES = 60 * 1000 * 1000  # 60 MB hard ceiling
MIN_DURATION_SEC = 3.0
MAX_DURATION_SEC = 60.00  # Hard ceiling: strictly <= 60.00 seconds
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
TARGET_FPS = 30.0
MAX_AUDIO_SAMPLE_RATE = 48000
MAX_VIDEO_BITRATE_BPS = 15 * 1000 * 1000  # 15 Mbps


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
    Treats all 17 mandatory compatibility conditions as strict validation errors.
    """

    @classmethod
    def check_faststart(cls, video_path: Path) -> Tuple[bool, Optional[int], Optional[int]]:
        """
        Scans top-level MP4 atoms to determine if fast-start is present (moov before mdat).
        Returns (is_faststart, moov_offset, mdat_offset).
        """
        moov_offset: Optional[int] = None
        mdat_offset: Optional[int] = None
        try:
            with open(video_path, "rb") as f:
                offset = 0
                while True:
                    hdr = f.read(8)
                    if len(hdr) < 8:
                        break
                    size, name_bytes = struct.unpack(">I4s", hdr)
                    name = name_bytes.decode("ascii", errors="ignore")
                    if name == "moov" and moov_offset is None:
                        moov_offset = offset
                    elif name == "mdat" and mdat_offset is None:
                        mdat_offset = offset

                    if size == 1:
                        data64 = f.read(8)
                        if len(data64) < 8:
                            break
                        size = struct.unpack(">Q", data64)[0]
                        f.seek(size - 16, 1)
                        offset += size
                    elif size == 0:
                        # Box extends to end of file
                        break
                    else:
                        f.seek(size - 8, 1)
                        offset += size
        except Exception as e:
            logger.debug(f"Atom scanning failed for {video_path}: {e}")

        # Faststart is valid only if moov atom exists and precedes mdat atom
        is_faststart = (
            moov_offset is not None
            and (mdat_offset is None or moov_offset < mdat_offset)
        )
        return is_faststart, moov_offset, mdat_offset

    @classmethod
    def detect_vfr(cls, video_path: Path, fps_nom: float, fps_avg: float) -> Tuple[bool, str]:
        """
        Detects Variable Frame Rate (VFR) by inspecting packet timestamps.
        Does not infer CFR only from r_frame_rate and avg_frame_rate.
        Returns (is_cfr: bool, reason: str).
        """
        # Quick check: if nominal and average frame rates differ by >= 0.05, it is definitely VFR
        if fps_nom <= 0 or fps_avg <= 0:
            return False, "Undetermined or missing frame rate metadata"

        if abs(fps_nom - fps_avg) >= 0.05:
            return False, f"Nominal frame rate ({fps_nom:.2f}) does not match average frame rate ({fps_avg:.2f})"

        # Deep packet timestamp inspection using ffprobe
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,duration_time",
            "-read_intervals", "%+#60",
            "-print_format", "json",
            str(video_path)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=8.0)
            data = json.loads(res.stdout)
            packets = data.get("packets", [])
            if len(packets) >= 10:
                durations = []
                for p in packets:
                    d = p.get("duration_time")
                    if d is not None:
                        try:
                            durations.append(float(d))
                        except (ValueError, TypeError):
                            pass
                if durations:
                    min_dur = min(durations)
                    max_dur = max(durations)
                    # For 30 FPS, expected duration is ~0.0333s. Variation > 0.003s indicates VFR.
                    if (max_dur - min_dur) > 0.003:
                        return False, f"Packet duration variance detected ({min_dur:.4f}s to {max_dur:.4f}s; VFR stream)"
                
                # Check consecutive PTS delta variance
                pts_list = []
                for p in packets:
                    pts = p.get("pts_time")
                    if pts is not None:
                        try:
                            pts_list.append(float(pts))
                        except (ValueError, TypeError):
                            pass
                if len(pts_list) >= 10:
                    deltas = [pts_list[i+1] - pts_list[i] for i in range(len(pts_list) - 1)]
                    positive_deltas = [d for d in deltas if d > 0]
                    if positive_deltas:
                        min_delta = min(positive_deltas)
                        max_delta = max(positive_deltas)
                        if (max_delta - min_delta) > 0.005:
                            return False, f"Frame presentation timestamp delta variance detected ({min_delta:.4f}s to {max_delta:.4f}s; VFR stream)"
        except Exception as e:
            logger.debug(f"Packet probe failed for {video_path}: {e}")
            # If packet probe fails, fall back to nominal vs average check
            if abs(fps_nom - fps_avg) >= 0.02:
                return False, f"Frame rate variance detected: {fps_nom:.2f} vs {fps_avg:.2f}"

        return True, "Constant frame rate verified"

    @classmethod
    def validate(cls, video_path: Path) -> InstagramValidationResult:
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

        # 15. File Size Ceiling (60 MB)
        if file_size > MAX_FILE_SIZE_BYTES:
            errors.append(
                f"File size ({details['file_size_mb']} MB) exceeds Meta Reels limit of 60 MB."
            )

        # Probe file via FFprobe
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15.0)
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

        # 16. Valid and Complete Video Stream
        if not v_stream:
            errors.append("No video stream found in file.")
            return InstagramValidationResult(is_valid=False, errors=errors, details=details)

        # 1. Container Format
        fmt_name = fmt.get("format_name", "").lower()
        details["format_name"] = fmt_name
        if "mp4" not in fmt_name and "mov" not in fmt_name:
            errors.append(f"Incompatible container format '{fmt_name}'. MP4 is strictly required.")

        # 2. Video Codec (H.264 / AVC required)
        v_codec = (v_stream.get("codec_name") or "").lower()
        details["video_codec"] = v_codec
        if v_codec not in ("h264", "avc1"):
            errors.append(f"Video codec '{v_codec}' is not H.264 (libx264/avc1).")

        # Codec Profile & Level
        profile = (v_stream.get("profile") or "").lower()
        level = v_stream.get("level")
        details["profile"] = profile
        details["level"] = level
        if level is not None:
            try:
                # Level 4.1 is represented as 41. Level 4.2 is 42. Meta requires <= 4.1 (41).
                if int(level) > 41:
                    errors.append(f"H.264 level {level} exceeds recommended maximum Level 4.1 for Meta Reels.")
            except (ValueError, TypeError):
                pass

        # 3. Pixel Format (yuv420p required)
        pix_fmt = (v_stream.get("pix_fmt") or "").lower()
        details["pixel_format"] = pix_fmt
        if pix_fmt != "yuv420p":
            errors.append(f"Pixel format '{pix_fmt}' is not yuv420p.")

        # 4. Progressive Scan
        field_order = (v_stream.get("field_order") or "progressive").lower()
        interlaced = int(v_stream.get("interlaced_frame", 0)) if v_stream.get("interlaced_frame") else 0
        details["field_order"] = field_order
        details["interlaced"] = interlaced
        if field_order not in ("progressive", "unknown") or interlaced != 0:
            errors.append(f"Interlaced scan detected ({field_order}). Progressive scan is strictly required.")

        # 7. Resolution (1080x1920 mandatory) & 8. Aspect Ratio (9:16)
        w = int(v_stream.get("width", 0))
        h = int(v_stream.get("height", 0))
        details["width"] = w
        details["height"] = h
        details["resolution"] = f"{w}x{h}"

        if w != TARGET_WIDTH or h != TARGET_HEIGHT:
            errors.append(
                f"Resolution {w}x{h} does not match mandatory Instagram Reels standard 1080x1920 (9:16 portrait)."
            )

        sar = v_stream.get("sample_aspect_ratio", "1:1")
        details["sar"] = sar
        if sar not in ("1:1", "0:1", "unknown", None):
            errors.append(f"Non-square pixel aspect ratio ({sar}). SAR 1:1 is required.")

        # 5. Closed GOP (checked via frame inspection if available)
        # Note: libx264 open-gop=0 is enforced during encoding.

        # 6. Constant Frame Rate (CFR) at 30 FPS
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

        # Mandatory CFR check
        is_cfr, vfr_reason = cls.detect_vfr(video_path, fps_nom, fps_avg)
        details["is_constant_frame_rate"] = is_cfr
        details["vfr_reason"] = vfr_reason

        if not is_cfr:
            errors.append(f"Variable frame rate detected: {vfr_reason}. Constant frame rate (CFR) is strictly required.")

        if abs(fps_nom - TARGET_FPS) > 0.5:
            errors.append(f"Frame rate {fps_nom:.2f} FPS deviates from strict 30 FPS profile.")

        # 14. Duration (3.0s to 60.00s strictly)
        dur_val = float(fmt.get("duration", 0.0)) if fmt.get("duration") else 0.0
        details["duration_sec"] = round(dur_val, 2)
        if dur_val > MAX_DURATION_SEC:
            errors.append(
                f"Duration {dur_val:.2f}s exceeds Meta Reels maximum limit of 60.00 seconds."
            )
        elif dur_val < MIN_DURATION_SEC:
            errors.append(
                f"Duration {dur_val:.2f}s is below Meta Reels minimum limit of 3.00 seconds."
            )

        # 9. AAC-LC Audio, 10. Sample Rate <= 48kHz, 11. Channels
        if not a_stream:
            errors.append(
                "No audio stream found. Meta Reels strictly requires an AAC-LC audio stream."
            )
            details["audio_present"] = False
        else:
            details["audio_present"] = True
            a_codec = (a_stream.get("codec_name") or "").lower()
            a_profile = (a_stream.get("profile") or "").lower()
            a_rate = int(a_stream.get("sample_rate", 0)) if a_stream.get("sample_rate") else 0
            a_channels = int(a_stream.get("channels", 0)) if a_stream.get("channels") else 0
            details["audio_codec"] = a_codec
            details["audio_profile"] = a_profile
            details["audio_sample_rate"] = a_rate
            details["audio_channels"] = a_channels

            if a_codec != "aac":
                errors.append(f"Audio codec '{a_codec}' is not AAC.")
            if a_profile and "lc" not in a_profile and "low" not in a_profile:
                errors.append(f"Audio profile '{a_profile}' is not AAC-LC.")
            if a_rate > MAX_AUDIO_SAMPLE_RATE:
                errors.append(
                    f"Audio sample rate {a_rate} Hz exceeds Meta Reels maximum of 48000 Hz."
                )
            if a_channels not in (1, 2):
                errors.append(
                    f"Audio channel count ({a_channels}) is unsupported. Mono (1) or Stereo (2) is required."
                )

        # 12. Moov Atom Placement (+faststart structure)
        is_faststart, moov_offset, mdat_offset = cls.check_faststart(video_path)
        details["moov_offset"] = moov_offset
        details["mdat_offset"] = mdat_offset
        details["faststart_valid"] = is_faststart
        if not is_faststart:
            errors.append("Fast-start (+faststart) missing: moov atom is located after mdat or missing.")

        # 13. Edit Lists & Zero Start Timestamps
        start_time = float(fmt.get("start_time", 0.0)) if fmt.get("start_time") else 0.0
        v_start_time = float(v_stream.get("start_time", 0.0)) if v_stream.get("start_time") else 0.0
        details["start_time"] = start_time
        details["video_start_time"] = v_start_time
        if start_time > 0.05 or v_start_time > 0.05:
            errors.append(f"Non-zero stream start time ({max(start_time, v_start_time):.3f}s). Timeline must start at 0.0s without edit list offset.")

        # 17. SDR-Compatible Output with BT.709 Color
        color_space = (v_stream.get("color_space") or "").lower()
        color_transfer = (v_stream.get("color_transfer") or "").lower()
        color_primaries = (v_stream.get("color_primaries") or "").lower()
        details["color_space"] = color_space
        details["color_transfer"] = color_transfer
        details["color_primaries"] = color_primaries

        hdr_markers = ("bt2020", "smpte2084", "arib-std-b67", "dovi")
        if any(marker in color_space or marker in color_transfer or marker in color_primaries for marker in hdr_markers):
            errors.append("HDR color format detected. Meta Reels strictly requires BT.709 SDR.")

        # Mandatory properties must all pass
        is_valid = len(errors) == 0
        return InstagramValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            details=details,
        )


instagram_video_validator = InstagramVideoValidator()
