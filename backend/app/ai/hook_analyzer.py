"""
AI Opening Hook Analyzer for YouTube Shorts & Reels.
Analyzes the first 3 seconds of footage to evaluate visual hook strength, motion intensity,
scene clarity, curiosity, gameplay event importance, and detects dead footage, menus, and loading screens.
"""

import json
import logging
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional
try:
    from PIL import Image, ImageStat
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    Image = None
    ImageStat = None

from backend.app.core.config import settings

logger = logging.getLogger("gaming_uncut.hook_analyzer")

class HookAnalyzer:
    def __init__(self):
        self._ffmpeg_bin = self._find_ffmpeg()

    def _find_ffmpeg(self) -> str:
        bin_path = shutil.which("ffmpeg")
        if bin_path:
            return bin_path
        candidates = [
            Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path("ffmpeg.exe")
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return "ffmpeg"

    def extract_hook_frames(
        self,
        video_path: Path,
        start_sec: float = 0.0,
        window_sec: float = 3.0,
        num_frames: int = 5
    ) -> List[Path]:
        """Extracts evenly spaced frames across the opening window (default: first 3.0 seconds)."""
        if not video_path.exists():
            return []

        temp_dir = Path(tempfile.mkdtemp(prefix="hook_frames_"))
        extracted_paths: List[Path] = []
        step = max(0.2, window_sec / float(num_frames))

        for i in range(num_frames):
            t = round(start_sec + (i * step), 2)
            out_img = temp_dir / f"frame_{i:02d}_{t:.2f}s.jpg"
            cmd = [
                self._ffmpeg_bin,
                "-y",
                "-ss", str(t),
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "2",
                "-vf", "scale=640:-1",
                str(out_img)
            ]
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5.0,
                    check=False
                )
                if out_img.exists() and out_img.stat().st_size > 100:
                    extracted_paths.append(out_img)
            except Exception as e:
                logger.debug(f"Frame extraction at {t}s failed: {e}")

        return extracted_paths

    def compute_visual_metrics(self, frame_paths: List[Path]) -> Dict[str, Any]:
        """
        Analyzes frame images for motion intensity, scene clarity, dead footage,
        black frames, and static menus/loading screens.
        """
        if not HAS_PIL or not frame_paths:
            return {
                "motion_intensity": 5.0,
                "clarity_of_scene": 5.0,
                "has_dead_footage": False,
                "has_menu_or_loading": False,
                "brightness_avg": 50.0,
                "frame_variance": 10.0
            }

        images = []
        for p in frame_paths:
            try:
                img = Image.open(p).convert("L")  # Convert to grayscale for fast luminance analysis
                images.append(img)
            except Exception:
                pass

        if not images:
            return {
                "motion_intensity": 5.0,
                "clarity_of_scene": 5.0,
                "has_dead_footage": False,
                "has_menu_or_loading": False,
                "brightness_avg": 50.0,
                "frame_variance": 10.0
            }

        # 1. Average luminance & contrast
        brightness_scores = []
        std_devs = []
        for img in images:
            stat = ImageStat.Stat(img)
            brightness_scores.append(stat.mean[0])
            std_devs.append(stat.stddev[0])

        avg_brightness = sum(brightness_scores) / len(brightness_scores)
        avg_std_dev = sum(std_devs) / len(std_devs)

        # 2. Inter-frame pixel difference (Motion Intensity)
        diff_scores = []
        for i in range(len(images) - 1):
            img_a = images[i]
            img_b = images[i + 1]
            # Fast pixel difference
            stat_a = ImageStat.Stat(img_a)
            stat_b = ImageStat.Stat(img_b)
            diff = abs(stat_a.mean[0] - stat_b.mean[0]) + abs(stat_a.stddev[0] - stat_b.stddev[0])
            diff_scores.append(diff)

        avg_diff = sum(diff_scores) / len(diff_scores) if diff_scores else 0.0

        # Motion intensity on 0.0 - 10.0 scale
        motion_score = min(10.0, round((avg_diff / 8.0) * 10.0, 1))
        # Clarity of scene on 0.0 - 10.0 scale based on edge contrast / variance
        clarity_score = min(10.0, max(1.0, round((avg_std_dev / 65.0) * 10.0, 1)))

        # 3. Detect Dead Footage / Black Screen / Menus
        has_black_screen = avg_brightness < 12.0
        has_static_screen = avg_diff < 1.2
        has_dead_footage = has_black_screen or (has_static_screen and avg_std_dev < 15.0)
        # Menus or loading screens typically have low motion but high text/UI contrast
        has_menu_or_loading = has_static_screen and avg_std_dev >= 20.0 and avg_diff < 1.5

        return {
            "motion_intensity": max(1.0, motion_score),
            "clarity_of_scene": clarity_score,
            "has_dead_footage": has_dead_footage,
            "has_menu_or_loading": has_menu_or_loading,
            "has_black_screen": has_black_screen,
            "brightness_avg": round(avg_brightness, 1),
            "frame_variance": round(avg_std_dev, 1),
            "inter_frame_motion": round(avg_diff, 2)
        }

    def analyze_opening_hook(
        self,
        video_path: Path,
        start_sec: float = 0.0,
        climax_sec: Optional[float] = None,
        duration_window: float = 3.0,
        source_title: str = "",
        game_name: Optional[str] = None,
        event_type: str = "gameplay_sequence"
    ) -> Dict[str, Any]:
        """
        Comprehensive hook analysis scoring the opening 3 seconds on:
        1. Immediate visual action (0-10)
        2. Motion intensity (0-10)
        3. Clarity of the scene (0-10)
        4. Curiosity (0-10)
        5. Gameplay event importance (0-10)
        6. Time until payoff (seconds)
        """
        frame_paths = self.extract_hook_frames(video_path, start_sec=start_sec, window_sec=duration_window)
        visual_metrics = self.compute_visual_metrics(frame_paths)

        # Cleanup temp frames
        if frame_paths:
            try:
                shutil.rmtree(frame_paths[0].parent, ignore_errors=True)
            except Exception:
                pass

        # Calculate time until payoff
        if climax_sec is not None and climax_sec >= start_sec:
            payoff_seconds = round(float(climax_sec - start_sec), 2)
        else:
            payoff_seconds = round(min(duration_window, 2.5), 2)

        # Score event importance (0-10)
        high_stakes_keywords = ["clutch", "boss", "escape", "stunt", "crash", "parry", "elimination", "wanted", "heist", "overtake"]
        ev_lower = (event_type or "").lower()
        if any(k in ev_lower for k in high_stakes_keywords):
            event_importance = 9.0
        elif any(k in (source_title or "").lower() for k in high_stakes_keywords):
            event_importance = 8.5
        else:
            event_importance = 7.0

        # Score curiosity (0-10)
        # Curiosity is high when there is immediate motion and payoff is within 3-5 seconds
        if payoff_seconds <= 3.5 and visual_metrics["motion_intensity"] >= 6.0:
            curiosity = 8.5
        elif visual_metrics["has_dead_footage"] or visual_metrics["has_menu_or_loading"]:
            curiosity = 2.0
        else:
            curiosity = 6.5

        # Score immediate visual action (0-10)
        immediate_action = visual_metrics["motion_intensity"]
        if visual_metrics["has_dead_footage"]:
            immediate_action = 1.0
        elif visual_metrics["has_menu_or_loading"]:
            immediate_action = 2.5

        # Check slow intro
        has_slow_intro = bool(payoff_seconds > 4.5 or (visual_metrics["motion_intensity"] < 3.0 and not visual_metrics["has_dead_footage"]))

        # Check unnecessary transitions
        has_unnecessary_transition = visual_metrics.get("has_black_screen", False)

        # Calculate Weighted Overall Hook Score (0.0 to 10.0)
        # Weights: Immediate Action 25%, Motion 20%, Clarity 15%, Curiosity 20%, Event Importance 20%
        raw_score = (
            (immediate_action * 0.25) +
            (visual_metrics["motion_intensity"] * 0.20) +
            (visual_metrics["clarity_of_scene"] * 0.15) +
            (curiosity * 0.20) +
            (event_importance * 0.20)
        )

        # Penalties for fatal hook blockers
        penalties = 0.0
        warnings = []
        if visual_metrics["has_dead_footage"]:
            penalties += 4.5
            warnings.append("Dead footage or static freeze detected in opening 3 seconds.")
        if visual_metrics["has_menu_or_loading"]:
            penalties += 4.0
            warnings.append("Menu or loading screen detected at start of clip.")
        if has_slow_intro:
            penalties += 1.5
            warnings.append(f"Slow intro: time until event payoff is {payoff_seconds}s (ideal: <= 3.0s).")
        if has_unnecessary_transition:
            penalties += 2.0
            warnings.append("Black screen or obscuring transition in opening frames.")

        final_hook_score = max(1.0, min(10.0, round(raw_score - penalties, 1)))

        # Suggest trim offset if opening is compromised
        recommended_trim_offset = 0.0
        if visual_metrics["has_dead_footage"] or visual_metrics["has_menu_or_loading"] or final_hook_score < 6.0:
            recommended_trim_offset = min(3.0, max(1.0, round(payoff_seconds * 0.5, 1)))

        return {
            "overall_hook_score": final_hook_score,
            "dimensions": {
                "immediate_visual_action": round(immediate_action, 1),
                "motion_intensity": round(visual_metrics["motion_intensity"], 1),
                "clarity_of_scene": round(visual_metrics["clarity_of_scene"], 1),
                "curiosity": round(curiosity, 1),
                "gameplay_event_importance": round(event_importance, 1),
                "time_until_payoff_sec": payoff_seconds
            },
            "diagnostics": {
                "has_slow_intro": has_slow_intro,
                "has_dead_footage": visual_metrics["has_dead_footage"],
                "has_menu_or_loading": visual_metrics["has_menu_or_loading"],
                "has_unnecessary_transition": has_unnecessary_transition
            },
            "recommended_trim_offset": recommended_trim_offset,
            "quality_verdict": "PASS" if final_hook_score >= 6.0 and not visual_metrics["has_dead_footage"] else "FAIL",
            "warnings": warnings
        }

hook_analyzer = HookAnalyzer()
