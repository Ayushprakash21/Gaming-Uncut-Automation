"""
Publishing Quality Gate for Gaming Uncut Shorts & Reels.
Enforces strict pre-publication quality standards.
Prevents automatic publishing of clips with:
1. Weak opening (hook score < 6.0/10)
2. Critical opening flaws evaluated independently (scene clarity < 5.0/10 or low visual action < 5.0/10)
3. Excessive payoff delay (> 5.0s) or slow intros causing immediate viewer drop-off
4. Excessive dead time, pause screens, or loading/menu screens
5. Unclear or generic gameplay event archetypes
6. Generic or repetitive title patterns
7. Unverified game identity
Sends non-qualifying clips for operator review ('needs_review') with actionable trimming recommendations.
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from backend.app.models.schema import GeneratedClip, ClipMetadata, PublishingJob
from backend.app.ai.seo_metadata_engine import GENERIC_BANNED_TITLE_PATTERNS

logger = logging.getLogger("gaming_uncut.quality_gate")

MIN_HOOK_SCORE_THRESHOLD = 6.0
MIN_OPENING_CLARITY_THRESHOLD = 5.0
MIN_OPENING_ACTION_THRESHOLD = 5.0
MAX_PAYOFF_DELAY_SEC = 5.0
MIN_GAME_CONFIDENCE_THRESHOLD = 0.70


class PublishingQualityGate:
    def __init__(
        self,
        min_hook_score: float = MIN_HOOK_SCORE_THRESHOLD,
        min_clarity: float = MIN_OPENING_CLARITY_THRESHOLD,
        min_action: float = MIN_OPENING_ACTION_THRESHOLD,
        max_payoff_delay: float = MAX_PAYOFF_DELAY_SEC,
        min_game_confidence: float = MIN_GAME_CONFIDENCE_THRESHOLD,
    ):
        self.min_hook_score = min_hook_score
        self.min_clarity = min_clarity
        self.min_action = min_action
        self.max_payoff_delay = max_payoff_delay
        self.min_game_confidence = min_game_confidence

    def evaluate_clip(
        self,
        clip: GeneratedClip,
        metadata: Optional[ClipMetadata] = None,
        hook_analysis: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluates a clip against mandatory publishing quality criteria.
        Evaluates the opening 3 seconds independently to ensure a high overall weighted
        score never conceals critical opening weaknesses (low clarity, delayed payoff, or slow intro).
        Returns gate decision ('AUTO_PUBLISH_APPROVED' or 'MANUAL_REVIEW_REQUIRED')
        with detailed rejection reasons and trimming recommendations.
        """
        reasons: List[str] = []
        recommendations: List[str] = []

        # -------------------------------------------------------------------------
        # Extract Hook & Diagnostic Data
        # -------------------------------------------------------------------------
        hook_data = hook_analysis or {}
        if not hook_data and clip.hook_analysis_json:
            try:
                hook_data = json.loads(clip.hook_analysis_json)
            except Exception:
                hook_data = {}

        # 1. Overall Hook Score
        hook_score = None
        if "overall_hook_score" in hook_data:
            hook_score = float(hook_data["overall_hook_score"])
        elif clip.hook_score is not None:
            hook_score = float(clip.hook_score)
        else:
            hook_score = 7.0

        if hook_score < self.min_hook_score:
            reasons.append(
                f"Weak opening hook: overall score is {hook_score:.1f}/10 (minimum required: {self.min_hook_score:.1f}/10)."
            )

        # 2. Independent Evaluation of Opening 3 Seconds (Zero Concealment)
        dims = hook_data.get("dimensions") or {}
        if dims:
            clarity = float(dims.get("clarity_of_scene", 7.0))
            action = float(dims.get("immediate_visual_action", 7.0))
            payoff_delay = float(dims.get("time_until_payoff_sec", 3.0))

            # Critical opening clarity check
            if clarity < self.min_clarity:
                reasons.append(
                    f"Low opening scene clarity: {clarity:.1f}/10 (minimum required: {self.min_clarity:.1f}/10). "
                    "The opening 3 seconds are visually blurry or ambiguous, which causes instant viewer swipe-away."
                )
                recommendations.append(
                    "Trim opening to where scene becomes clearly visible, or switch to a high-clarity cut."
                )

            # Critical opening visual movement check
            if action < self.min_action:
                reasons.append(
                    f"Insufficient opening visual action: {action:.1f}/10 (minimum required: {self.min_action:.1f}/10). "
                    "Lacks immediate visual movement in the first 3 seconds to capture viewer attention."
                )

            # Configurable maximum payoff delay check
            if payoff_delay > self.max_payoff_delay:
                reasons.append(
                    f"Excessive payoff delay: climax occurs at {payoff_delay:.1f}s (maximum allowed for auto-publish: {self.max_payoff_delay:.1f}s). "
                    "Setup is too long for vertical Shorts retention."
                )
                trim_suggest = round(payoff_delay - 2.5, 1)
                recommendations.append(
                    f"Trim the opening by ~{trim_suggest}s to bring the action payoff into the first 3 seconds, "
                    "or select the punchy candidate cut (15–25s)."
                )

        # 3. Dead Footage, Menus & Loading Screens
        diag = hook_data.get("diagnostics") or {}
        has_dead_footage = bool(diag.get("has_dead_footage", False))
        has_menu = bool(diag.get("has_menu_or_loading", False))
        has_slow_intro = bool(diag.get("has_slow_intro", False))

        if has_dead_footage:
            reasons.append("Excessive dead footage or static freeze detected in opening 3 seconds.")
            recommendations.append("Trim dead footage or black frames from clip start.")
        if has_menu:
            reasons.append("Pause menu, title card, or loading screen detected at start of clip.")
            recommendations.append("Trim menu or loading screen sequence to begin directly with live gameplay.")
        if has_slow_intro and not any("Excessive payoff delay" in r for r in reasons):
            reasons.append("Slow intro detected: gameplay setup is too protracted before the core event begins.")

        # 4. Candidate Cuts Availability Check
        cand_cuts = []
        if clip.candidate_cuts_json:
            try:
                cand_cuts = json.loads(clip.candidate_cuts_json)
            except Exception:
                cand_cuts = []

        punchy_cut = next((c for c in cand_cuts if c.get("format_name") == "punchy_action"), None)
        if punchy_cut and any("payoff delay" in r.lower() or "slow intro" in r.lower() for r in reasons):
            recommendations.append(
                f"Alternative available: Use candidate cut '{punchy_cut.get('format_label', 'Punchy Cut')}' "
                f"({punchy_cut.get('duration_sec', 0)}s) with payoff at {punchy_cut.get('lead_in_sec', 0)}s."
            )

        # 5. Evaluate Gameplay Event Clarity
        event_type = ""
        if clip.highlight and clip.highlight.event_type:
            event_type = clip.highlight.event_type
        elif metadata and metadata.topic:
            event_type = metadata.topic

        unclear_event_keywords = [
            "gameplay sequence", "action climax", "full gameplay cut",
            "gaming highlight", "raw capture", "generic"
        ]
        ev_lower = (event_type or "").lower().strip()
        if not ev_lower or any(u in ev_lower for u in unclear_event_keywords):
            reasons.append(
                f"Unclear gameplay event archetype: '{event_type or 'None'}'. Must identify a specific action (clutch, escape, boss duel, stunt, crash)."
            )

        # 6. Evaluate Title Specificity (No Generic Fallbacks)
        title = metadata.title if metadata else (clip.metadata_rel.title if clip.metadata_rel else "")
        t_lower = (title or "").lower().strip()
        if not t_lower:
            reasons.append("Missing title. Every clip must possess a factual, descriptive title.")
        else:
            for pattern in GENERIC_BANNED_TITLE_PATTERNS:
                if re.search(pattern, t_lower):
                    reasons.append(
                        f"Generic title pattern detected: '{title}'. Titles must describe actual gameplay events."
                    )
                    break

        # 7. Evaluate Game Identity Verification
        detected_game = "Unknown Game"
        confidence = 0.0
        user_game = None

        if metadata:
            detected_game = metadata.detected_game or "Unknown Game"
            confidence = float(metadata.game_confidence or 0.0)
            user_game = metadata.user_game_name
        elif clip.metadata_rel:
            detected_game = clip.metadata_rel.detected_game or "Unknown Game"
            confidence = float(clip.metadata_rel.game_confidence or 0.0)
            user_game = clip.metadata_rel.user_game_name

        has_verified_game = bool(
            (detected_game != "Unknown Game" and confidence >= self.min_game_confidence) or
            (user_game and user_game.strip())
        )

        if not has_verified_game:
            reasons.append(
                f"Unverified game identity: detected '{detected_game}' with only {int(confidence*100)}% confidence. Game must be verified before auto-publish."
            )

        # Gate Verdict
        passed = len(reasons) == 0
        verdict = "AUTO_PUBLISH_APPROVED" if passed else "MANUAL_REVIEW_REQUIRED"
        recommended_status = "approved" if passed else "needs_review"

        logger.info(
            f"[QualityGate] Clip #{clip.id}: Verdict={verdict} (Hook={hook_score:.1f}, "
            f"Game='{detected_game}', Issues={len(reasons)})"
        )

        return {
            "clip_id": clip.id,
            "gate_passed": passed,
            "gate_decision": verdict,
            "recommended_approval_status": recommended_status,
            "hook_score": hook_score,
            "detected_game": detected_game,
            "event_type": event_type,
            "title": title,
            "reasons": reasons,
            "recommendations": recommendations,
        }

    def apply_quality_gate(self, db: Session, clip_id: int) -> Dict[str, Any]:
        """Applies quality gate to a clip and persists decision to ClipMetadata and PublishingJob."""
        clip = db.query(GeneratedClip).filter_by(id=clip_id).first()
        if not clip:
            raise ValueError(f"Clip {clip_id} not found.")

        meta = clip.metadata_rel
        eval_result = self.evaluate_clip(clip, metadata=meta)

        if meta:
            meta.quality_gate_passed = eval_result["gate_passed"]
            meta.quality_gate_reasons_json = json.dumps(eval_result["reasons"])

        # Guard publishing jobs associated with this clip
        jobs = db.query(PublishingJob).filter_by(clip_id=clip.id).all()
        for j in jobs:
            if not eval_result["gate_passed"]:
                # Force status to needs_review if currently pending or approved
                if j.approval_status in ("approved", "auto_approved", "pending"):
                    j.approval_status = "needs_review"
                    logger.warning(
                        f"[QualityGate] PublishingJob #{j.id} for Clip #{clip.id} downgraded to 'needs_review': {eval_result['reasons']}"
                    )

        db.commit()
        return eval_result


publishing_quality_gate = PublishingQualityGate()
