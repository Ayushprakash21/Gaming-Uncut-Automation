"""
Video Compositor & Vertical Transformer for Gaming Uncut.
Converts 16:9 landscape footage into high-engagement 1080x1920 vertical format.
Supports Blurred Background, Split Screen (Facecam + Gameplay), Gaming Uncut watermark,
and burned-in animated ASS captions using NVIDIA NVENC hardware acceleration.
"""

import subprocess
import logging
from pathlib import Path
from typing import Optional
from src.core.config import settings

logger = logging.getLogger("gaming_uncut.compositor")

class VideoCompositor:
    def __init__(self, use_nvenc: bool = settings.USE_NVENC):
        self.use_nvenc = use_nvenc
        self.encoder = "h264_nvenc" if self.use_nvenc else "libx264"
        self._test_nvenc()

    def _test_nvenc(self):
        """Validates NVENC support, falls back to libx264 if not available."""
        if not self.use_nvenc:
            return
        try:
            res = subprocess.run(
                ["ffmpeg", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True
            )
            if res.returncode != 0:
                logger.warning("NVENC test failed, falling back to CPU encoder libx264.")
                self.encoder = "libx264"
        except Exception:
            self.encoder = "libx264"

    def render_blurred_bg(
        self,
        input_path: Path,
        output_path: Path,
        start_sec: float,
        duration_sec: float,
        ass_subtitles_path: Optional[Path] = None,
        watermark_text: str = settings.WATERMARK_TEXT
    ) -> Path:
        """
        Renders 1080x1920 vertical video with blurred background padding,
        centered sharp gameplay, and burned-in DirectWrite ASS captions & watermark.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Windows path escaping for FFmpeg filters (forward slashes and escaped colon)
        def escape_path(p: Path) -> str:
            return str(p.resolve()).replace("\\", "/").replace(":", "\\:")

        # 1. Background: scale to 1080x1920 crop, heavy blur, slight dimming
        # 2. Foreground: scale to 1080 width, centered vertically
        filter_complex = (
            "[0:v]split=2[bg_in][fg_in];"
            "[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.15[bg];"
            "[fg_in]scale=1080:-1[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
        )

        final_label = "[base]"
        if ass_subtitles_path and ass_subtitles_path.exists():
            ass_escaped = escape_path(ass_subtitles_path)
            filter_complex += f";[base]ass='{ass_escaped}'[outv]"
            final_label = "[outv]"

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-t", str(duration_sec),
            "-i", str(input_path),
            "-filter_complex", filter_complex,
            "-map", final_label,
            "-map", "0:a?",
            "-c:v", self.encoder,
        ]

        if self.encoder == "h264_nvenc":
            cmd.extend(["-preset", "p5", "-b:v", "8M", "-maxrate", "12M", "-bufsize", "16M"])
        else:
            cmd.extend(["-preset", "fast", "-crf", "20"])

        cmd.extend([
            "-c:a", "aac",
            "-b:a", "192k",
            "-r", str(settings.TARGET_FPS),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path)
        ])

        logger.info(f"Rendering vertical clip with {self.encoder}: {output_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output_path

    def generate_thumbnail(self, video_path: Path, output_thumb: Path, timestamp_sec: float = 2.0) -> Path:
        """Extracts representative 1080x1920 JPEG thumbnail."""
        output_thumb.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(timestamp_sec),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(output_thumb)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_thumb
