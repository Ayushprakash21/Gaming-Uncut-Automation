"""
Automatic Highlight Optimizer for YouTube Shorts & Vertical Reels.
Generates candidate clip boundaries around genuine gameplay events (clutches, escapes, crashes, stunts, boss encounters).
Produces alternative 15–25-second (punchy) and 25–40-second (story/tension) cuts while preserving vital context.
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from backend.app.ai.hook_analyzer import hook_analyzer

logger = logging.getLogger("gaming_uncut.highlight_optimizer")

# High-priority genuine gameplay event archetypes
PREFERRED_EVENT_TYPES = [
    "clutch",
    "escape",
    "near_miss",
    "crash",
    "stunt",
    "unexpected_event",
    "boss_encounter",
    "funny_gameplay"
]

class HighlightOptimizer:
    def classify_gameplay_event(
        self,
        event_name: str,
        context_text: str = ""
    ) -> str:
        """Categorizes raw event descriptions into preferred Shorts gameplay archetypes."""
        text = f"{event_name} {context_text}".lower()
        if any(w in text for w in ["escape", "pursuit", "police", "chase", "wanted", "getaway"]):
            return "escape"
        elif any(w in text for w in ["clutch", "1v", "defend", "overtake", "last stand"]):
            return "clutch"
        elif any(w in text for w in ["crash", "wreck", "collision", "pileup"]):
            return "crash"
        elif any(w in text for w in ["stunt", "drift", "jump", "ramp", "flip", "barrel roll"]):
            return "stunt"
        elif any(w in text for w in ["near miss", "close call", "narrow", "barely", "millimeter"]):
            return "near_miss"
        elif any(w in text for w in ["boss", "parry", "duel", "strike", "combat", "showdown"]):
            return "boss_encounter"
        elif any(w in text for w in ["funny", "glitch", "hilarious", "fail", "unexpected", "wtf"]):
            return "funny_gameplay"
        return "unexpected_event"

    def optimize_highlight_boundaries(
        self,
        video_path: Path,
        climax_sec: float,
        video_duration: float,
        raw_event_type: str,
        source_title: str = "",
        game_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generates alternative candidate clip boundaries around genuine gameplay events.
        Produces:
        - 15-25 second cut (instant action payoff)
        - 25-40 second cut (suspense setup + climax + aftermath)
        Evaluates context and opening 3-second hook strength for each cut.
        """
        climax = max(0.0, min(float(climax_sec), float(video_duration)))
        event_archetype = self.classify_gameplay_event(raw_event_type, source_title)

        candidate_cuts: List[Dict[str, Any]] = []

        # 1. Generate 15–25-second Cut ("Punchy Action Cut")
        target_dur_short = min(22.0, video_duration)
        if video_duration >= 15.0:
            # Lead-in: 3.5s to 5.0s before climax so the viewer grasps context before the big moment
            ideal_lead_in = 4.0
            start_short = max(0.0, climax - ideal_lead_in)
            if start_short + target_dur_short > video_duration:
                start_short = max(0.0, video_duration - target_dur_short)
            end_short = min(video_duration, start_short + target_dur_short)
            actual_dur_short = round(end_short - start_short, 2)

            # Analyze opening hook for short cut
            hook_res_short = hook_analyzer.analyze_opening_hook(
                video_path=video_path,
                start_sec=start_short,
                climax_sec=climax,
                duration_window=3.0,
                source_title=source_title,
                game_name=game_name,
                event_type=event_archetype
            )

            # If dead footage or slow intro detected, apply suggested trim offset
            if hook_res_short.get("recommended_trim_offset", 0.0) > 0.0:
                offset = hook_res_short["recommended_trim_offset"]
                if start_short + offset < climax:
                    start_short = round(start_short + offset, 2)
                    actual_dur_short = round(end_short - start_short, 2)

            candidate_cuts.append({
                "format_name": "punchy_action",
                "format_label": "15–25s Punchy Cut",
                "target_range": "15-25s",
                "start_sec": start_short,
                "end_sec": end_short,
                "duration_sec": actual_dur_short,
                "climax_sec": climax,
                "lead_in_sec": round(climax - start_short, 2),
                "aftermath_sec": round(end_short - climax, 2),
                "event_archetype": event_archetype,
                "context_summary": f"Quick {round(climax - start_short, 1)}s setup leading directly to {event_archetype} at {climax:.1f}s with {round(end_short - climax, 1)}s payoff.",
                "hook_score": hook_res_short["overall_hook_score"],
                "hook_analysis": hook_res_short
            })

        # 2. Generate 25–40-second Cut ("Narrative / Tension Cut")
        target_dur_long = min(35.0, video_duration)
        if video_duration >= 25.0:
            # Lead-in: 10.0s to 14.0s before climax to build suspense and story
            ideal_lead_in = 11.0
            start_long = max(0.0, climax - ideal_lead_in)
            if start_long + target_dur_long > video_duration:
                start_long = max(0.0, video_duration - target_dur_long)
            end_long = min(video_duration, start_long + target_dur_long)
            actual_dur_long = round(end_long - start_long, 2)

            hook_res_long = hook_analyzer.analyze_opening_hook(
                video_path=video_path,
                start_sec=start_long,
                climax_sec=climax,
                duration_window=3.0,
                source_title=source_title,
                game_name=game_name,
                event_type=event_archetype
            )

            if hook_res_long.get("recommended_trim_offset", 0.0) > 0.0:
                offset = hook_res_long["recommended_trim_offset"]
                if start_long + offset < climax:
                    start_long = round(start_long + offset, 2)
                    actual_dur_long = round(end_long - start_long, 2)

            candidate_cuts.append({
                "format_name": "story_tension",
                "format_label": "25–40s Tension Cut",
                "target_range": "25-40s",
                "start_sec": start_long,
                "end_sec": end_long,
                "duration_sec": actual_dur_long,
                "climax_sec": climax,
                "lead_in_sec": round(climax - start_long, 2),
                "aftermath_sec": round(end_long - climax, 2),
                "event_archetype": event_archetype,
                "context_summary": f"Tension-building {round(climax - start_long, 1)}s setup leading to climax at {climax:.1f}s followed by {round(end_long - climax, 1)}s resolution.",
                "hook_score": hook_res_long["overall_hook_score"],
                "hook_analysis": hook_res_long
            })

        # Select primary cut based on hook score and event suitability
        if candidate_cuts:
            # Sort by hook score descending
            sorted_cuts = sorted(candidate_cuts, key=lambda c: c["hook_score"], reverse=True)
            primary = sorted_cuts[0]
        else:
            # Fallback if video is under 15s
            dur = round(video_duration, 2)
            primary = {
                "format_name": "source_length",
                "format_label": "Full Length Cut",
                "target_range": "<15s",
                "start_sec": 0.0,
                "end_sec": dur,
                "duration_sec": dur,
                "climax_sec": climax,
                "lead_in_sec": climax,
                "aftermath_sec": round(dur - climax, 2),
                "event_archetype": event_archetype,
                "context_summary": f"Complete footage clip of {dur}s.",
                "hook_score": 7.0,
                "hook_analysis": {}
            }
            candidate_cuts.append(primary)

        return {
            "primary_cut": primary,
            "candidate_cuts": candidate_cuts,
            "event_archetype": event_archetype,
            "is_preferred_event": event_archetype in PREFERRED_EVENT_TYPES
        }

highlight_optimizer = HighlightOptimizer()
