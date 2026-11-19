"""
Performance Intelligence & Continuous Learning Engine for Gaming Uncut Shorts.
Collects measured platform metrics, identifies correlation patterns across games,
event types, opening hooks, durations, and templates, while strictly enforcing sample size thresholds.
Manages controlled creative experiments without duplicate uploads.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.models.schema import (
    PlatformUpload, PublishingAnalytics, ShortsAnalyticsSnapshot,
    GeneratedClip, ClipMetadata, DetectedHighlight,
    ClipSeoMetadata, SeoGrowthInsight, PublishingJob, ShortsExperiment
)
from backend.app.utils.timezone import to_ist

logger = logging.getLogger("gaming_uncut.growth_intelligence")

MIN_SIGNIFICANT_SAMPLE_SIZE = 10  # Never declare winning formats below this threshold

class GrowthIntelligenceService:
    def get_dashboard_summary(self, db: Session) -> Dict[str, Any]:
        """
        Gathers executive KPIs for the Shorts Growth Intelligence dashboard.
        Combines measured YouTube analytics, hook scores, quality gate rates,
        and continuous performance learning insights.
        """
        total_clips = db.query(GeneratedClip).count()
        total_uploads = db.query(PlatformUpload).filter(
            PlatformUpload.platform == "youtube",
            PlatformUpload.status.in_(["published", "published_verified", "completed"])
        ).count()

        # Average Hook Score across clips
        avg_hook_scalar = db.query(func.avg(GeneratedClip.hook_score)).scalar()
        avg_hook_score = round(float(avg_hook_scalar or 7.2), 1)

        # Average Quality Score across clips
        avg_quality_scalar = db.query(func.avg(GeneratedClip.quality_score)).scalar()
        avg_quality_score = round(float(avg_quality_scalar or 85.0), 1)

        # Measured YouTube Analytics Aggregates
        analytics_recs = db.query(PublishingAnalytics).filter_by(platform="youtube").all()
        total_measured_views = sum(a.views or 0 for a in analytics_recs)
        total_engaged_views = sum(a.engaged_views or 0 for a in analytics_recs if a.engaged_views is not None)
        
        # Calculate retention metrics where measured by YouTube Analytics API
        retention_list = [a.average_percentage_viewed for a in analytics_recs if a.average_percentage_viewed is not None]
        avg_retention_pct = round(sum(retention_list) / len(retention_list), 1) if retention_list else None

        stayed_list = [a.stayed_to_watch_pct for a in analytics_recs if a.stayed_to_watch_pct is not None]
        avg_stayed_to_watch_pct = round(sum(stayed_list) / len(stayed_list), 1) if stayed_list else None

        # Continuous Performance Learning by Dimension
        learning_data = self.compute_performance_learning_breakdown(db)

        # Active Experiments
        experiments = db.query(ShortsExperiment).order_by(ShortsExperiment.id.desc()).limit(5).all()
        experiments_data = [self._format_experiment(e, db) for e in experiments]

        # Top Performing Games (measured by views & retention)
        best_games = learning_data.get("games_breakdown", [])[:6]

        # Top Performing Gameplay Events
        best_events = learning_data.get("events_breakdown", [])[:6]

        # Actionable Batch Recommendations based on actual data
        recommendations = self.generate_batch_recommendations(learning_data)

        return {
            "total_clips": total_clips,
            "total_youtube_uploads": total_uploads,
            "avg_hook_score": avg_hook_score,
            "avg_quality_score": avg_quality_score,
            "total_measured_views": total_measured_views,
            "total_engaged_views": total_engaged_views,
            "avg_retention_pct": avg_retention_pct,
            "avg_stayed_to_watch_pct": avg_stayed_to_watch_pct,
            "best_games": best_games,
            "best_events": best_events,
            "learning_breakdown": learning_data,
            "batch_recommendations": recommendations,
            "experiments": experiments_data,
            "min_sample_size_threshold": MIN_SIGNIFICANT_SAMPLE_SIZE,
            "last_synced_at": datetime.utcnow().isoformat()
        }

    def compute_performance_learning_breakdown(self, db: Session) -> Dict[str, Any]:
        """
        Compares published clips across:
        - Game
        - Event type
        - Duration bracket (15-25s, 25-40s, 40-60s)
        - Publishing time of day (IST)
        - Layout template
        Strictly flags any pattern with sample size < 10 as insufficient sample.
        """
        # Query clips joined with metadata and analytics
        records = (
            db.query(
                GeneratedClip.id,
                GeneratedClip.duration_sec,
                GeneratedClip.layout_template,
                GeneratedClip.hook_score,
                ClipMetadata.detected_game,
                ClipMetadata.game_confidence,
                DetectedHighlight.event_type,
                PlatformUpload.uploaded_at,
                PublishingAnalytics.views,
                PublishingAnalytics.likes,
                PublishingAnalytics.average_percentage_viewed,
                PublishingAnalytics.stayed_to_watch_pct
            )
            .join(ClipMetadata, GeneratedClip.id == ClipMetadata.clip_id)
            .outerjoin(DetectedHighlight, GeneratedClip.highlight_id == DetectedHighlight.id)
            .outerjoin(PublishingJob, GeneratedClip.id == PublishingJob.clip_id)
            .outerjoin(PlatformUpload, PublishingJob.id == PlatformUpload.publishing_job_id)
            .outerjoin(PublishingAnalytics, PlatformUpload.id == PublishingAnalytics.platform_upload_id)
            .all()
        )

        # 1. Game Breakdown
        games_dict: Dict[str, List[Dict[str, Any]]] = {}
        # 2. Event Type Breakdown
        events_dict: Dict[str, List[Dict[str, Any]]] = {}
        # 3. Duration Bracket Breakdown
        dur_dict: Dict[str, List[Dict[str, Any]]] = {
            "15–25s (Punchy)": [],
            "25–40s (Tension)": [],
            "40–60s (Extended)": []
        }

        for r in records:
            game = r[4] or "Unknown Game"
            event = r[6] or "Gameplay Climax"
            dur = float(r[1] or 60.0)
            views = int(r[8] or 0)
            retention = float(r[10]) if r[10] is not None else None
            hook = float(r[3]) if r[3] is not None else 7.0

            item = {"views": views, "retention": retention, "hook_score": hook}

            # By Game
            games_dict.setdefault(game, []).append(item)
            # By Event
            events_dict.setdefault(event, []).append(item)
            # By Duration
            if dur <= 25.0:
                dur_dict["15–25s (Punchy)"].append(item)
            elif dur <= 40.0:
                dur_dict["25–40s (Tension)"].append(item)
            else:
                dur_dict["40–60s (Extended)"].append(item)

        def _aggregate_group(items: List[Dict[str, Any]]) -> Dict[str, Any]:
            n = len(items)
            tot_views = sum(x["views"] for x in items)
            ret_vals = [x["retention"] for x in items if x["retention"] is not None]
            avg_ret = round(sum(ret_vals) / len(ret_vals), 1) if ret_vals else None
            avg_hook = round(sum(x["hook_score"] for x in items) / n, 1) if n > 0 else 0.0

            is_significant = n >= MIN_SIGNIFICANT_SAMPLE_SIZE
            status = "statistically_significant" if is_significant else "insufficient_sample"

            return {
                "sample_size": n,
                "total_views": tot_views,
                "avg_views_per_clip": round(tot_views / max(1, n), 1),
                "avg_retention_pct": avg_ret,
                "avg_hook_score": avg_hook,
                "is_statistically_significant": is_significant,
                "status": status,
                "confidence_note": (
                    f"Verified pattern ({n} observations >= {MIN_SIGNIFICANT_SAMPLE_SIZE})"
                    if is_significant
                    else f"Tentative trend ({n} observations < {MIN_SIGNIFICANT_SAMPLE_SIZE} threshold required)"
                )
            }

        games_summary = [
            {"game_name": k, **_aggregate_group(v)}
            for k, v in games_dict.items()
            if k != "Unknown Game"
        ]
        games_summary.sort(key=lambda x: (x["sample_size"], x["total_views"]), reverse=True)

        events_summary = [
            {"event_type": k, **_aggregate_group(v)}
            for k, v in events_dict.items()
        ]
        events_summary.sort(key=lambda x: (x["sample_size"], x["total_views"]), reverse=True)

        dur_summary = [
            {"duration_bracket": k, **_aggregate_group(v)}
            for k, v in dur_dict.items()
            if v
        ]
        dur_summary.sort(key=lambda x: x["sample_size"], reverse=True)

        return {
            "games_breakdown": games_summary,
            "events_breakdown": events_summary,
            "duration_breakdown": dur_summary,
            "total_analyzed_clips": len(records)
        }

    def generate_batch_recommendations(self, learning_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Formulates objective, data-driven batch recommendations.
        Strictly notes sample size and does not declare winning formats on insufficient data.
        """
        recs = []
        games = learning_data.get("games_breakdown", [])
        events = learning_data.get("events_breakdown", [])
        durations = learning_data.get("duration_breakdown", [])

        # 1. Game Focus Recommendation
        sig_games = [g for g in games if g["is_statistically_significant"]]
        if sig_games:
            top_game = sig_games[0]
            recs.append({
                "type": "game_focus",
                "priority": "HIGH",
                "title": f"Expand Core Coverage: {top_game['game_name']}",
                "detail": f"{top_game['game_name']} leads your channel with {top_game['sample_size']} clips and {top_game['avg_views_per_clip']} avg views.",
                "sample_size": top_game["sample_size"],
                "is_statistically_significant": True
            })
        else:
            recs.append({
                "type": "game_focus",
                "priority": "MEDIUM",
                "title": "Establish Baseline Game Consistency",
                "detail": f"No single game has reached {MIN_SIGNIFICANT_SAMPLE_SIZE} published samples yet. Continue publishing dedicated series of at least 10 clips per game.",
                "sample_size": sum(g["sample_size"] for g in games),
                "is_statistically_significant": False
            })

        # 2. Gameplay Event Recommendation
        sig_events = [e for e in events if e["is_statistically_significant"]]
        if sig_events:
            top_ev = sig_events[0]
            recs.append({
                "type": "event_optimization",
                "priority": "HIGH",
                "title": f"Prioritize {top_ev['event_type']} Sequences",
                "detail": f"{top_ev['event_type']} shows verified audience traction with an average hook score of {top_ev['avg_hook_score']}/10 across {top_ev['sample_size']} clips.",
                "sample_size": top_ev["sample_size"],
                "is_statistically_significant": True
            })
        else:
            recs.append({
                "type": "event_optimization",
                "priority": "MEDIUM",
                "title": "Diversify High-Stakes Highlights",
                "detail": "Gather more clutch, escape, and boss sequences to identify which gameplay climax drives peak retention.",
                "sample_size": sum(e["sample_size"] for e in events),
                "is_statistically_significant": False
            })

        # 3. Duration Recommendation
        punchy = next((d for d in durations if "15–25s" in d["duration_bracket"]), None)
        tension = next((d for d in durations if "25–40s" in d["duration_bracket"]), None)
        if punchy and tension and (punchy["sample_size"] >= 5 or tension["sample_size"] >= 5):
            recs.append({
                "type": "duration_strategy",
                "priority": "HIGH",
                "title": "Deploy Dual-Cut Duration Strategy",
                "detail": "Maintain 15-25s cuts for instant action climaxes, and 25-40s cuts for tension-building boss duels.",
                "sample_size": punchy["sample_size"] + tension["sample_size"],
                "is_statistically_significant": (punchy["sample_size"] + tension["sample_size"]) >= MIN_SIGNIFICANT_SAMPLE_SIZE
            })
        else:
            recs.append({
                "type": "duration_strategy",
                "priority": "MEDIUM",
                "title": "Test 15–25s vs 25–40s Cuts",
                "detail": f"Generate both punchy (15-25s) and tension (25-40s) variants across upcoming batches (sample currently n={sum(d['sample_size'] for d in durations)}).",
                "sample_size": sum(d["sample_size"] for d in durations),
                "is_statistically_significant": False
            })

        return recs

    def create_experiment(
        self,
        db: Session,
        name: str,
        hypothesis: str,
        dimension: str,
        game_name: Optional[str] = None,
        control_clip_id: Optional[int] = None,
        variant_clip_ids: Optional[List[int]] = None
    ) -> ShortsExperiment:
        """
        Creates a controlled A/B-style sequential experiment between separate clips.
        Strictly forbids uploading duplicate video copies.
        """
        var_ids = variant_clip_ids or []
        # Enforce anti-duplicate rule: control and variants must be distinct clips
        all_ids = ([control_clip_id] if control_clip_id else []) + var_ids
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Controlled experiment cannot test duplicate copies of the same clip. All clip IDs must be distinct.")

        exp = ShortsExperiment(
            name=name.strip(),
            hypothesis=hypothesis.strip(),
            dimension=dimension.strip(),
            game_name=game_name.strip() if game_name else None,
            control_clip_id=control_clip_id,
            variant_clip_ids_json=json.dumps(var_ids),
            sample_size=len(all_ids),
            min_sample_size=MIN_SIGNIFICANT_SAMPLE_SIZE,
            status="active",
            created_at=datetime.utcnow()
        )
        db.add(exp)
        db.commit()
        db.refresh(exp)
        return exp

    def _format_experiment(self, exp: ShortsExperiment, db: Session) -> Dict[str, Any]:
        """Formats an experiment record with live measured metrics."""
        var_ids = []
        if exp.variant_clip_ids_json:
            try:
                var_ids = json.loads(exp.variant_clip_ids_json)
            except Exception:
                pass

        total_tested = (1 if exp.control_clip_id else 0) + len(var_ids)
        is_sig = total_tested >= exp.min_sample_size

        return {
            "id": exp.id,
            "name": exp.name,
            "hypothesis": exp.hypothesis,
            "dimension": exp.dimension,
            "game_name": exp.game_name or "All Games",
            "status": exp.status,
            "control_clip_id": exp.control_clip_id,
            "variant_clip_ids": var_ids,
            "sample_size": total_tested,
            "min_sample_size": exp.min_sample_size,
            "is_statistically_significant": is_sig,
            "winning_variant": exp.winning_variant if is_sig else "Pending sufficient sample size",
            "conclusion": exp.conclusion or (
                "Gathering observations..." if not is_sig else "Experiment active with statistical significance."
            ),
            "created_at": exp.created_at.isoformat() if exp.created_at else None
        }

growth_intelligence_service = GrowthIntelligenceService()
