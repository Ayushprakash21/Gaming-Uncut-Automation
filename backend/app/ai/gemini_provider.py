"""
Gemini Multimodal AI Provider for Gaming Uncut Automation.
Uses Google Gemini API for deep gameplay understanding, climax selection, and metadata generation.
Includes resilient local heuristic fallback when offline or before API key is provided.
"""

import json
import logging
from typing import List, Dict, Any, Optional

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from backend.app.ai.base_provider import BaseAIProvider
from backend.app.core.config import settings
from backend.app.security.vault import vault

logger = logging.getLogger("gaming_uncut.gemini")

class GeminiAIProvider(BaseAIProvider):
    def __init__(self):
        self._client = None

    def _get_client(self) -> Optional[Any]:
        if genai is None:
            return None
        api_key = vault.get_gemini_api_key() or settings.model_config.get("env_file")
        if not api_key:
            import os
            api_key = os.environ.get("GEMINI_API_KEY")
        if api_key:
            return genai.Client(api_key=api_key)
        return None

    def analyze_gameplay_highlights(
        self,
        transcript_text: str,
        scene_timestamps: List[float],
        audio_peaks: List[Dict[str, Any]],
        video_duration: float
    ) -> List[Dict[str, Any]]:
        client = self._get_client()

        # If client not available, run intelligent local heuristic
        if not client:
            logger.info("Gemini API key not configured. Using local signal-fusion engine for highlight detection.")
            return self._local_heuristic_highlights(scene_timestamps, audio_peaks, video_duration)

        prompt = f"""
You are the lead editor for 'Gaming Uncut' (Tagline: NO FILTER. JUST GAMEPLAY).
Analyze the following gameplay telemetry and commentary transcript from a gaming video of duration {video_duration:.1f}s.
Identify the top 1 to 3 most intense, clutch, or funny gameplay highlights suitable for a 60-second vertical Reel.

Telemetry Data:
- Scene Transitions (Cut timestamps): {scene_timestamps[:30]}
- Significant Audio Decibel Spikes: {audio_peaks[:30]}
- Commentary Transcript: "{transcript_text[:4000]}"

Requirements:
- Target highlight duration: exactly between 30 and 60 seconds (ideally 50-58s for maximum retention).
- Score each highlight from 0 to 100 based on intensity, action density, and commentary hype.
- Classify the event (e.g., 'Clutch 1v3', 'Boss Battle Victory', 'Epic Escape', 'Funny Fail', 'High Velocity Chase').
- Suggest an opening hook text (max 6 words).

Return ONLY a JSON array of objects with the exact schema:
[
  {{
    "start_sec": float,
    "end_sec": float,
    "duration_sec": float,
    "score": float,
    "event_type": str,
    "explanation": str,
    "hook_suggestion": str,
    "caption_suggestion": str,
    "confidence": float
  }}
]
"""
        try:
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            data = json.loads(response.text)
            if isinstance(data, list) and len(data) > 0:
                logger.info(f"Gemini identified {len(data)} high-confidence gameplay highlights.")
                return data
        except Exception as e:
            logger.error(f"Gemini API highlight analysis failed: {e}. Falling back to local heuristic.")

        return self._local_heuristic_highlights(scene_timestamps, audio_peaks, video_duration)

    def _local_heuristic_highlights(
        self,
        scene_timestamps: List[float],
        audio_peaks: List[Dict[str, Any]],
        video_duration: float
    ) -> List[Dict[str, Any]]:
        """Intelligent local signal-fusion fallback."""
        if not audio_peaks and not scene_timestamps:
            # Fallback to center or first segment
            end = min(video_duration, settings.TARGET_DURATION)
            return [{
                "start_sec": 0.0,
                "end_sec": end,
                "duration_sec": end,
                "score": 75.0,
                "event_type": "High Intensity Action",
                "explanation": "Primary gameplay sequence extracted from source footage.",
                "hook_suggestion": "WATCH TILL THE END!",
                "caption_suggestion": "Unreal gameplay sequence.",
                "confidence": 0.85
            }]

        # Find cluster of peaks
        sorted_peaks = sorted(audio_peaks, key=lambda x: x.get("db", -100), reverse=True)
        top_moments = []

        for p in sorted_peaks[:5]:
            peak_ts = p["ts"]
            # Center peak around 70% into the 58s clip
            start = max(0.0, peak_ts - (settings.TARGET_DURATION * 0.70))
            end = min(video_duration, start + settings.TARGET_DURATION)
            duration = end - start

            if duration < settings.MIN_DURATION:
                continue

            # Snap start to closest scene cut if within 2 seconds
            for cut in scene_timestamps:
                if abs(cut - start) < 2.0:
                    start = cut
                    end = min(video_duration, start + settings.TARGET_DURATION)
                    duration = end - start
                    break

            # Deduplicate overlaps
            overlap = any(not (end < m["start_sec"] or start > m["end_sec"]) for m in top_moments)
            if not overlap:
                top_moments.append({
                    "start_sec": round(start, 2),
                    "end_sec": round(end, 2),
                    "duration_sec": round(duration, 2),
                    "score": round(min(98.0, 70.0 + (p.get("db", -20) + 20) * 1.5), 1),
                    "event_type": "Clutch Action Climax",
                    "explanation": f"High decibel explosion/commentary spike detected at {peak_ts:.1f}s.",
                    "hook_suggestion": "NO FILTER. JUST GAMEPLAY.",
                    "caption_suggestion": "Wait for this insane moment!",
                    "confidence": 0.90
                })
                if len(top_moments) >= 3:
                    break

        return top_moments or [{
            "start_sec": 0.0,
            "end_sec": min(video_duration, settings.TARGET_DURATION),
            "duration_sec": min(video_duration, settings.TARGET_DURATION),
            "score": 75.0,
            "event_type": "Full Gameplay Cut",
            "explanation": "Standard high-energy segment.",
            "hook_suggestion": "EPIC MOMENT",
            "caption_suggestion": "Full throttle gaming.",
            "confidence": 0.80
        }]

    def generate_reel_metadata(
        self,
        gameplay_summary: str,
        highlight_transcript: str,
        clip_duration: float
    ) -> Dict[str, Any]:
        client = self._get_client()

        if client:
            prompt = f"""
You are the social media director for 'Gaming Uncut' (NO FILTER. JUST GAMEPLAY).
Generate optimized metadata for a 60-second vertical gaming Reel / Short.

Context:
- Gameplay Event: {gameplay_summary}
- Spoken Commentary: "{highlight_transcript[:2000]}"
- Brand Tagline: "NO FILTER. JUST GAMEPLAY."

Output Requirements:
- Title: Engaging, authentic, under 70 characters (NO misleading clickbait).
- Topic: Accurate game genre or category (e.g., 'FPS Gameplay', 'Battle Royale', 'Clutch Moments', 'Open World').
- Description: Concise 2-3 sentences explaining the play.
- Hashtags: 5-8 relevant gaming hashtags (e.g. #GamingUncut, #Shorts, #Gameplay, #Gamer).
- Pinned Comment: A question encouraging viewer debate (e.g. "Rate this play from 1 to 10!").

Return ONLY JSON:
{{
  "title": str,
  "topic": str,
  "description": str,
  "hashtags": str,
  "pinned_comment": str,
  "youtube_title": str,
  "youtube_desc": str,
  "instagram_caption": str,
  "facebook_caption": str
}}
"""
            try:
                res = client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.3)
                )
                data = json.loads(res.text)
                if "title" in data:
                    return data
            except Exception as e:
                logger.error(f"Gemini metadata generation error: {e}")

        # High quality default fallback
        clean_title = f"{gameplay_summary} | Gaming Uncut"
        hashtags = "#GamingUncut #Shorts #Gaming #Gameplay #Clutch #Gamer"
        desc = f"Watch this high-intensity gameplay moment from Gaming Uncut. No filter, just pure gameplay."
        
        return {
            "title": clean_title[:95],
            "topic": "Gaming Highlights",
            "description": desc,
            "hashtags": hashtags,
            "pinned_comment": "What would you have done in this situation? Let us know below! 👇",
            "youtube_title": f"{clean_title[:80]} #Shorts",
            "youtube_desc": f"{desc}\n\n{hashtags}\n\nNO FILTER. JUST GAMEPLAY.",
            "instagram_caption": f"{clean_title}\n\n{desc}\n\n{hashtags}",
            "facebook_caption": f"{clean_title}\n\n{desc}\n\n{hashtags}"
        }

gemini_provider = GeminiAIProvider()
