"""
Publishing Quality Gate for Gaming Uncut Shorts & Reels.
Enforces strict pre-publication quality standards.
Prevents automatic publishing of clips with:
1. Weak opening (hook score < 6.0/10)
2. Excessive dead time or loading/menu screens
3. Unclear or generic gameplay event
4. Generic or repetitive title
5. Unverified game identity
Sends non-qualifying clips for operator review.
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
MIN_GAME_CONFIDENCE_THRESHOLD = 0.70

class PublishingQualityGate:
    def evaluate_clip(
        self,
        clip: GeneratedClip,
        metadata: Optional[ClipMetadata] = None,
        hook_analysis: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Evaluates a clip against the 5 mandatory publishing quality criteria.
        Returns gate decision ('AUTO_PUBLISH_APPROVED' or 'MANUAL_REVIEW_REQUIRED')
        with detailed rejection reasons.
        """
        reasons: List[str] = []

        # 1. Evaluate Opening Hook Score
        hook_score = None
        if hook_analysis and "overall_hook_score" in hook_analysis:
            hook_score = float(hook_analysis["overall_hook_score"])
        elif clip.hook_score is not None:
            hook_score = float(clip.hook_score)

        if hook_score is None:
            # Parse from stored JSON if present
            if clip.hook_analysis_json:
                try:
                    ha = json.loads(clip.hook_analysis_json)
                    hook_score = float(ha.get("overall_hook_score", 7.0))
                except Exception:
                    hook_score = 7.0
            else:
                hook_score = 7.0

        if hook_score < MIN_HOOK_SCORE_THRESHOLD:
            reasons.append(
                f"Weak opening hook: score is {hook_score:.1f}/10 (minimum required: {MIN_HOOK_SCORE_THRESHOLD:.1f}/10)."
            )

        # 2. Evaluate Dead Footage & Menus
        has_dead_footage = False
        has_menu = False
        if hook_analysis and "diagnostics" in hook_analysis:
            diag = hook_analysis["diagnostics"]
            has_dead_footage = bool(diag.get("has_dead_footage", False))
            has_menu = bool(diag.get("has_menu_or_loading", False))
        elif clip.hook_analysis_json:
            try:
                ha = json.loads(clip.hook_analysis_json)
                diag = ha.get("diagnostics", {})
                has_dead_footage = bool(diag.get("has_dead_footage", False))
                has_menu = bool(diag.get("has_menu_or_loading", False))
            except Exception:
                pass

        if has_dead_footage:
            reasons.append("Excessive dead footage or static freeze detected in opening 3 seconds.")
        if has_menu:
            reasons.append("Pause menu, title card, or loading screen detected at start of clip.")

        # 3. Evaluate Gameplay Event Clarity
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
                f"Unclear gameplay event: '{event_type or 'None'}'. Must identify specific action (clutch, escape, boss duel, stunt, crash)."
            )

        # 4. Evaluate Title Specificity (No Generic Fallbacks)
        title = metadata.title if metadata else (clip.metadata_rel.title if clip.metadata_rel else "")
        t_lower = (title or "").lower().strip()
        if not t_lower:
            reasons.append("Missing title. Every clip must possess a factual, descriptive title.")
        else:
            for pattern in GENERIC_BANNED_TITLE_PATTERNS:
                if re.search(pattern, t_lower):
                    reasons.append(f"Generic title pattern detected: '{title}'. Titles must describe actual gameplay events.")
                    break

        # 5. Evaluate Game Identity Verification
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
            (detected_game != "Unknown Game" and confidence >= MIN_GAME_CONFIDENCE_THRESHOLD) or
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
            "reasons": reasons
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
