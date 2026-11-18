"""
Hardware-Accelerated 1080x1920 Video Rendering Engine for Gaming Uncut.
Mandatory YouTube Shorts Profile (1080x1920, 9:16 portrait, H.264/AAC, yuv420p, +faststart, <= 180s).
Supports:
- Intelligent conversion of landscape footage (blurred background fill or smart centered action crop).
- Native portrait footage passthrough (1080x1920 without unnecessary blur bars).
- Template A: Dynamic Gameplay (Smart Centered Action Crop)
- Template B: Blurred Background (Foreground Sharp + Background Gaussian Blur)
- Template C: Smart Split Layout (Upper Player Cam + Lower Gameplay)
Includes audio loudness normalization (loudnorm), NVENC encoding, and thumbnail extraction.
"""

import json
import subprocess
import logging
from pathlib import Path
from typing import Optional, Dict, Any
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

    def probe_video(self, video_path: Path) -> Dict[str, Any]:
        """Probes video dimensions, orientation, duration, and codec info with ffprobe."""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,size,format_name:stream=width,height,codec_name,codec_type,pix_fmt",
            "-of", "json",
            str(video_path)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(res.stdout)
            streams = data.get("streams", [])
            v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
            a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
            fmt = data.get("format", {})

            w = int(v_stream.get("width", 0))
            h = int(v_stream.get("height", 0))
            dur = float(fmt.get("duration", 0.0))

            return {
                "width": w,
                "height": h,
                "duration": dur,
                "video_codec": v_stream.get("codec_name", ""),
                "audio_codec": a_stream.get("codec_name", "") if a_stream else None,
                "pix_fmt": v_stream.get("pix_fmt", ""),
                "format_name": fmt.get("format_name", ""),
                "is_portrait": h > w,
                "is_landscape": w > h,
                "is_square": w == h and w > 0,
                "is_already_9_16": (w == 1080 and h == 1920) or (h > 0 and abs((w / h) - (9 / 16)) < 0.02)
            }
        except Exception as e:
            logger.warning(f"Could not probe video {video_path}: {e}")
            return {
                "width": 1920,
                "height": 1080,
                "duration": 0.0,
                "is_portrait": False,
                "is_landscape": True,
                "is_already_9_16": False
            }

    def render_reel(
        self,
        input_path: Path,
        output_path: Path,
        start_sec: float,
        duration_sec: float,
        template: str = "blurred_bg",
        ass_subtitles_path: Optional[Path] = None,
        normalize_audio: bool = True,
        job_id: Optional[int] = None
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Enforce maximum 180 seconds for YouTube Shorts compliance
        capped_duration = min(180.0, max(5.0, float(duration_sec)))

        # Probe input video orientation and dimensions
        probe = self.probe_video(input_path)
        is_already_portrait = probe.get("is_portrait", False) or probe.get("is_already_9_16", False)

        # Build filtergraph based on input orientation and chosen template
        if is_already_portrait:
            # Native portrait footage: scale to exactly 1080x1920 without black/blurred bars
            filter_complex = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[base]"
        elif template == "dynamic_gameplay":
            # Smart Center Action Crop: scale height to 1920, crop center 1080x1920
            filter_complex = "[0:v]scale=-2:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[base]"
        elif template == "split_layout":
            # Upper screen: top 30-40% of gameplay/webcam scaled to 1080x640
            # Lower screen: main action gameplay scaled to 1080x1280
            filter_complex = (
                "[0:v]split=2[cam_in][game_in];"
                "[cam_in]crop=iw:ih*0.4:0:0,scale=1080:640:force_original_aspect_ratio=increase,crop=1080:640,setsar=1[top];"
                "[game_in]scale=1080:1280:force_original_aspect_ratio=increase,crop=1080:1280,setsar=1[bottom];"
                "[top][bottom]vstack=inputs=2,setsar=1[base]"
            )
        else:
            # Default: blurred_bg (Gaussian blur background fill preserving sharp 16:9 foreground)
            filter_complex = (
                "[0:v]split=2[bg_in][fg_in];"
                "[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.15,setsar=1[bg];"
                "[fg_in]scale=1080:-2,setsar=1[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2:eval=init,setsar=1[base]"
            )

        final_label = "[base]"
        if ass_subtitles_path and ass_subtitles_path.exists():
            ass_escaped = self._escape_filter_path(ass_subtitles_path)
            filter_complex += f";[base]ass='{ass_escaped}',setsar=1[outv]"
            final_label = "[outv]"

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-t", str(capped_duration),
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

        # Mandatory Shorts Profile Flags: 1080x1920 9:16 H.264, AAC audio, yuv420p, +faststart
        # Enforce 48kHz stereo AAC and -shortest for strict YouTube Shorts and Meta/Instagram Reels compliance
        cmd.extend([
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            "-shortest",
            "-r", str(settings.TARGET_FPS),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path)
        ])

        logger.info(f"Rendering YouTube Shorts (1080x1920, 9:16, {capped_duration}s, {template}) with {self.encoder}: {output_path.name}")
        if job_id:
            from backend.app.services.pipeline_service import pipeline_service
            pipeline_service.run_cancellable_subprocess(
                cmd=cmd,
                job_id=job_id,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
        else:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return output_path

    def extract_thumbnail(self, video_path: Path, output_thumb: Path, timestamp_sec: float = 2.0, job_id: Optional[int] = None) -> Path:
        output_thumb.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(timestamp_sec),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(output_thumb)
        ]
        if job_id:
            from backend.app.services.pipeline_service import pipeline_service
            pipeline_service.run_cancellable_subprocess(
                cmd=cmd,
                job_id=job_id,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        else:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_thumb

video_renderer = VideoRenderer()
