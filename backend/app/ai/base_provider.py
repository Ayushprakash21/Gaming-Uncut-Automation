from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseAIProvider(ABC):
    @abstractmethod
    def analyze_gameplay_highlights(
        self,
        transcript_text: str,
        scene_timestamps: List[float],
        audio_peaks: List[Dict[str, Any]],
        video_duration: float,
        source_title: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Analyzes multimodal gameplay signals and returns ranked candidate moments
        with scores (0-100), event classifications, explanations, and hooks.
        """
        pass

    @abstractmethod
    def generate_reel_metadata(
        self,
        gameplay_summary: str,
        highlight_transcript: str,
        clip_duration: float
    ) -> Dict[str, Any]:
        """
        Generates engaging title, topic, description, hashtags, and platform-specific captions.
        """
        pass
