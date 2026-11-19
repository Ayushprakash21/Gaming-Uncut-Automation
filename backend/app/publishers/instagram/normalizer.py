"""
Instagram-Specific FFmpeg Normalization Engine.
Generates conservative, fully-compatible MP4 assets tailored for Meta Reels edge ingest:
- Container: MP4 (H.264 AVC using libx264, High Profile, Level 4.1)
- Resolution: 1080x1920 (9:16 vertical portrait with SAR 1:1)
- Constant Frame Rate: 30 FPS CFR (-r 30, -fps_mode cfr)
- Closed GOP: -g 60 -keyint_min 60 -sc_threshold 0 -x264-params open-gop=0
- Color space: BT.709 SDR (-color_primaries bt709 -color_trc bt709 -colorspace bt709)
- Audio: AAC-LC 128kbps, 48000 Hz stereo (-profile:a aac_low, synthetic silence generated if source has no audio)
- Duration: Strictly clamped between 3.0s and 59.5s (strictly prevents audio/video packet overflow >= 60.00s)
- Fast-start: -movflags +faststart (moov atom placed before mdat)
- Timestamps: -avoid_negative_ts make_zero -fflags +genpts (zero start time, no unsupported edit lists)
- Post-render verification: Rigorously verified via InstagramVideoValidator and full FFmpeg decode test
"""

import json
import logging
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.core.config import settings
from backend.app.publishers.instagram.validator import (
    InstagramVideoValidator,
    InstagramValidationResult,
    MAX_FILE_SIZE_BYTES,
)
from backend.app.video.ffmpeg_executor import safe_run_ffmpeg
from backend.app.video.worker import render_concurrency_lock

logger = logging.getLogger("gaming_uncut.instagram_normalizer")


class InstagramNormalizationError(Exception):
    """Raised when video normalization fails or fails post-render validation."""

    def __init__(self, message: str, errors: Optional[List[str]] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or []
        self.details = details or {}


class InstagramVideoNormalizer:
    """
    Normalizes video footage specifically for Instagram Reels compatibility.
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        default_policy: str = "crop",  # "crop" (smart center action crop) | "blur" (blurred background)
        preset: str = "veryfast",
        target_bitrate_mbps: float = 4.5,
        max_file_size_mb: float = 52.0,  # Safe ceiling under Meta's 60MB limit
    ):
        self.output_dir = output_dir or (settings.OUTPUT_DIR / "instagram")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.default_policy = default_policy
        self.preset = preset
        self.target_bitrate_mbps = target_bitrate_mbps
        self.max_file_size_mb = max_file_size_mb

    def probe_input(self, input_path: Path) -> Dict[str, Any]:
        """Probes basic stream metadata using ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(input_path),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15.0)
            probe = json.loads(res.stdout)
        except Exception as e:
            logger.error(f"Failed to probe video file {input_path}: {e}")
            raise ValueError(f"Corrupt or unreadable video file {input_path}: {e}")
        fmt = probe.get("format", {})
        streams = probe.get("streams", [])
        v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not v_stream:
            raise ValueError(f"Input file {input_path} contains no video stream.")

        width = int(v_stream.get("width", 0))
        height = int(v_stream.get("height", 0))
        duration = float(fmt.get("duration", 0.0))

        return {
            "width": width,
            "height": height,
            "duration": duration,
            "is_portrait": height > width,
            "is_landscape": width > height,
            "is_square": width == height and width > 0,
            "has_audio": a_stream is not None,
            "audio_codec": a_stream.get("codec_name") if a_stream else None,
            "audio_sample_rate": int(a_stream.get("sample_rate", 0)) if a_stream and a_stream.get("sample_rate") else None,
            "pix_fmt": v_stream.get("pix_fmt", "yuv420p"),
            "video_codec": v_stream.get("codec_name", "h264"),
        }

    def compute_dynamic_bitrate(self, duration_sec: float) -> Tuple[str, str, str]:
        """
        Dynamically calculates video bitrate, maxrate, and bufsize to guarantee
        that the resulting video file stays comfortably below the 60 MB limit.
        Target max size: 52 MB (54,525,952 bytes).
        Audio budget: 128 kbps (~16 KB/s).
        """
        effective_duration = max(3.0, min(60.0, duration_sec))
        max_bytes = self.max_file_size_mb * 1024 * 1024
        audio_bytes = (128 * 1000 / 8) * effective_duration
        container_overhead = 500 * 1024  # 500 KB container/moov headroom

        avail_video_bytes = max_bytes - audio_bytes - container_overhead
        avail_video_bits = avail_video_bytes * 8
        calc_bitrate_bps = avail_video_bits / effective_duration

        # Clamp between 2.0M and 5.5M for crisp gameplay while strictly under 52MB
        target_bps = min(self.target_bitrate_mbps * 1_000_000, max(2_000_000, calc_bitrate_bps))
        maxrate_bps = min(target_bps * 1.25, 6_000_000)
        bufsize_bps = target_bps * 2.0

        b_v = f"{int(target_bps / 1000)}k"
        maxrate = f"{int(maxrate_bps / 1000)}k"
        bufsize = f"{int(bufsize_bps / 1000)}k"

        logger.debug(
            f"Computed dynamic bitrate for duration={effective_duration:.2f}s: "
            f"-b:v {b_v}, -maxrate {maxrate}, -bufsize {bufsize}"
        )
        return b_v, maxrate, bufsize

    def normalize(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        policy: Optional[str] = None,
        force_render: bool = False,
    ) -> Path:
        """
        Normalizes the given video file for Instagram Reels.
        If the input file already strictly conforms to all Meta Reels specs,
        and force_render is False, it returns input_path directly.
        Otherwise, it executes the FFmpeg profile and verifies the output.
        """
        input_path = Path(input_path).resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # Check existing compliance first
        if not force_render:
            initial_val = InstagramVideoValidator.validate(input_path)
            if initial_val.is_valid:
                logger.info(
                    f"Video {input_path.name} already strictly adheres to Meta Reels specs. Bypassing re-encode."
                )
                return input_path

        policy = policy or self.default_policy
        probe = self.probe_input(input_path)
        duration = probe["duration"]
        has_audio = probe["has_audio"]
        is_landscape = probe["is_landscape"]

        # Clamp target duration: Meta maximum is 60.00s. Clamp to 59.5s to prevent audio packet tail overflow (e.g. 60.02s)
        # Minimum duration is 3.0s
        target_duration = min(59.5, max(3.0, duration))
        b_v, maxrate, bufsize = self.compute_dynamic_bitrate(target_duration)

        if output_path is None:
            output_name = f"{input_path.stem}_instagram.mp4"
            output_path = self.output_dir / output_name
        else:
            output_path = Path(output_path).resolve()

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build video filtergraph
        # Policy A: Smart Center Action Crop (scale to fill 1080x1920 without stretching, crop center)
        # Policy B: Blurred Background (preserves 16:9 foreground, fills pillarbox with blurred backdrop)
        # Portrait footage: scale to 1080:1920 maintaining aspect ratio, crop overflow
        if is_landscape and policy == "blur":
            v_filter = (
                "[0:v]split=2[bg_in][fg_in];"
                "[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.15,fps=30,format=yuv420p,setsar=1[bg];"
                "[fg_in]scale=1080:-2,fps=30,format=yuv420p,setsar=1[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2:eval=init,setsar=1[vout]"
            )
        else:
            # Standard & dynamic gameplay: scale maintaining aspect ratio without distortion, crop to exact 1080x1920
            v_filter = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                "fps=30,"
                "format=yuv420p,"
                "setsar=1[vout]"
            )

        # Handle duration < 3.0s: pad video to minimum 3 seconds
        if duration < 3.0:
            pad_needed = round(3.0 - duration, 2)
            v_filter = v_filter.replace("[vout]", f",tpad=stop_mode=clone:stop_duration={pad_needed}[vout]")

        cmd: List[str] = ["ffmpeg", "-y"]

        # Video input
        cmd.extend(["-i", str(input_path)])

        # Audio filter and mapping
        if has_audio:
            filter_complex = f"{v_filter};[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,atrim=0:{target_duration}[aout]"
        else:
            # Generate synthetic silent stereo 48kHz track
            cmd.extend(["-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={target_duration}"])
            filter_complex = f"{v_filter};[1:a]atrim=0:{target_duration}[aout]"

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            # Video codec settings (H.264 High Profile, Level 4.1, yuv420p)
            "-c:v", "libx264",
            "-preset", self.preset,
            "-profile:v", "high",
            "-level:v", "4.1",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-fps_mode", "cfr",
            # Closed GOP (2-second GOP at 30fps) and zero B-frame delay
            "-g", "60",
            "-keyint_min", "60",
            "-sc_threshold", "0",
            "-x264-params", "open-gop=0:colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-bf", "0",
            # Color characteristics (BT.709 SDR)
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-colorspace", "bt709",
            # Bitrate constraints
            "-b:v", b_v,
            "-maxrate", maxrate,
            "-bufsize", bufsize,
            # Audio codec settings (AAC-LC, 128k, 48000Hz, stereo)
            "-c:a", "aac",
            "-profile:a", "aac_low",
            "-b:a", "128k",
            "-ar", "48000",
            "-ac", "2",
            # Duration limit
            "-t", str(target_duration),
            # Monotonic timestamps, zero start time, fast-start
            "-avoid_negative_ts", "make_zero",
            "-fflags", "+genpts",
            "-movflags", "+faststart",
            str(output_path),
        ])

        logger.info(
            f"Encoding Instagram normalized Reel: {input_path.name} -> {output_path.name} "
            f"(target={target_duration:.1f}s, -b:v={b_v}, policy={policy})"
        )

        with render_concurrency_lock():
            exec_res = safe_run_ffmpeg(
                cmd=cmd,
                timeout=300.0,
                threads=2,
                output_path=output_path,
                min_output_bytes=1024,
                log_name="ig_normalizer"
            )

        if not exec_res.success:
            if output_path.exists():
                try:
                    output_path.unlink()
                except Exception:
                    pass
            raise InstagramNormalizationError(
                f"FFmpeg normalization failed (exit code {exec_res.returncode}): {exec_res.error_message}"
            )

        # Post-render FFprobe verification (Part 4)
        val_result = InstagramVideoValidator.validate(output_path)
        if not val_result.is_valid:
            logger.error(
                f"Normalized video failed Instagram post-render validation: {val_result.errors}"
            )
            if output_path.exists():
                try:
                    output_path.unlink()
                except Exception:
                    pass
            raise InstagramNormalizationError(
                f"Rendered file failed Meta Reels validation: {val_result.summary()}",
                errors=val_result.errors,
                details=val_result.details,
            )

        # Full decode integrity verification (Part 4)
        decode_cmd = ["ffmpeg", "-v", "error", "-i", str(output_path), "-f", "null", "-"]
        try:
            decode_res = subprocess.run(decode_cmd, capture_output=True, text=True, timeout=60.0)
            if decode_res.returncode != 0:
                if output_path.exists():
                    try:
                        output_path.unlink()
                    except Exception:
                        pass
                raise InstagramNormalizationError(
                    f"Rendered video failed full decode verification (exit code {decode_res.returncode}): {decode_res.stderr[:200]}"
                )
        except Exception as e:
            if output_path.exists():
                try:
                    output_path.unlink()
                except Exception:
                    pass
            raise InstagramNormalizationError(f"Video decode verification error: {e}")

        logger.info(
            f"Instagram video normalized & verified successfully: {output_path.name} "
            f"({val_result.details.get('file_size_mb')} MB, {val_result.details.get('duration_sec')}s, 30 FPS CFR, SAR 1:1, BT.709)"
        )
        return output_path


instagram_video_normalizer = InstagramVideoNormalizer()
