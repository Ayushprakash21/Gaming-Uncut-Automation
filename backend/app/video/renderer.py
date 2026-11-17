"""
Hardware-Accelerated 1080x1920 Video Rendering Engine for Gaming Uncut.
Supports:
- Template A: Dynamic Gameplay (Smart Centered Action Crop)
- Template B: Blurred Background (Foreground Sharp + Background Gaussian Blur)
- Template C: Smart Split Layout (Upper Player Cam + Lower Gameplay)
Includes audio loudness normalization (loudnorm), NVENC encoding, and thumbnail extraction.
"""

import subprocess
import logging
from pathlib import Path
from typing import Optional
from backend.app.core.config import settings

logger = logging.getLogger("gaming_uncut.renderer")

class VideoRenderer:
    def __init__(self, use_nvenc: bool = settings.USE_NVENC):
        self.use_nvenc = use_nvenc
        self.encoder = "h264_nvenc" if self.use_nvenc else "libx264"
        self._verify_encoder()

    def _verify_encoder(self):
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

    def _escape_filter_path(self, p: Path) -> str:
        """Escapes Windows paths for FFmpeg filtergraphs."""
        return str(p.resolve()).replace("\\", "/").replace(":", "\\:")

    def render_reel(
        self,
        input_path: Path,
        output_path: Path,
        start_sec: float,
        duration_sec: float,
        template: str = "blurred_bg",
        ass_subtitles_path: Optional[Path] = None,
        normalize_audio: bool = True
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build filtergraph based on template
        if template == "dynamic_gameplay":
            # Smart Center Crop: scale height to 1920, crop center 1080
            filter_complex = "[0:v]scale=-1:1920,crop=1080:1920[base]"
        elif template == "split_layout":
            # Upper screen: top 30% of gameplay / webcam scaled to 1080x640
            # Lower screen: main gameplay scaled to 1080x1280
            filter_complex = (
                "[0:v]split=2[cam_in][game_in];"
                "[cam_in]crop=iw:ih*0.4:0:0,scale=1080:640:force_original_aspect_ratio=increase,crop=1080:640[top];"
                "[game_in]scale=1080:1280:force_original_aspect_ratio=increase,crop=1080:1280[bottom];"
                "[top][bottom]vstack=inputs=2[base]"
            )
        else: # Default: blurred_bg
            filter_complex = (
                "[0:v]split=2[bg_in][fg_in];"
                "[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.15[bg];"
                "[fg_in]scale=1080:-1[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
            )

        final_label = "[base]"
        if ass_subtitles_path and ass_subtitles_path.exists():
            ass_escaped = self._escape_filter_path(ass_subtitles_path)
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

        # Audio loudness normalization
        if normalize_audio:
            cmd.extend(["-af", "loudnorm=I=-16:LRA=11:TP=-1.5"])

        cmd.extend([
            "-c:a", "aac",
            "-b:a", "192k",
            "-r", str(settings.TARGET_FPS),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path)
        ])

        logger.info(f"Rendering 60s Reel with {self.encoder} ({template}): {output_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output_path

    def extract_thumbnail(self, video_path: Path, output_thumb: Path, timestamp_sec: float = 2.0) -> Path:
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

video_renderer = VideoRenderer()
