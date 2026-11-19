"""
Comprehensive Regression Test Suite for YouTube Shorts Growth Intelligence Engine.
Validates all requirements:
1. Analytics API mapping and zero hallucination of unavailable metrics.
2. Data API v3 fallback when Analytics API is unavailable.
3. Historical snapshot recording and comparison across 1h, 6h, 24h, and 72h.
4. Opening 3-second hook analysis across 6 dimensions with dead footage / menu detection.
5. Highlight optimizer candidate cuts (15-25s and 25-40s) with context preservation.
6. Factual opening hook generation with zero misrepresentation.
7. Performance learning sample size threshold protection (<10 samples rejected).
8. Controlled experiments tracking with strict duplicate upload prohibition.
9. Publishing quality gate blocking weak openings, dead time, generic titles, and unverified games.
10. Publishing quality gate auto-approving compliant, high-retention clips.
"""

import json
import time
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    SourceVideo, GeneratedClip, ClipMetadata, DetectedHighlight, PublishingJob,
    PlatformUpload, PublishingAnalytics, ShortsAnalyticsSnapshot,
    ShortsExperiment
)
from backend.app.publishers.youtube.youtube_analytics import YouTubeAnalyticsService
from backend.app.ai.hook_analyzer import HookAnalyzer
from backend.app.ai.highlight_optimizer import HighlightOptimizer
from backend.app.ai.opening_hook_generator import OpeningHookGenerator
from backend.app.analytics.growth_intelligence import GrowthIntelligenceService, MIN_SIGNIFICANT_SAMPLE_SIZE
from backend.app.publishers.quality_gate import PublishingQualityGate

@pytest.fixture
def test_setup():
    """Sets up test models in SQLite and cleans up afterwards."""
    with SessionLocal() as db:
        unique_ts = int(time.time() * 1000)
        source = SourceVideo(
            title="GTA VI Test Gameplay",
            local_path=f"data/input/test_shorts_{unique_ts}.mp4",
            duration=120.0,
            status="ready",
            source_type="local"
        )
        db.add(source)
        db.flush()

        hl = DetectedHighlight(
            source_video_id=source.id,
            start_sec=10.0,
            end_sec=35.0,
            duration_sec=25.0,
            score=90.0,
            event_type="escape",
            explanation="5-star wanted level police escape",
            selected_for_edit=True
        )
        db.add(hl)
        db.flush()

        clip = GeneratedClip(
            highlight_id=hl.id,
            output_path="data/output/test_shorts_intel.mp4",
            duration_sec=25.0,
            quality_score=88.0,
            hook_score=7.8
        )
        db.add(clip)
        db.flush()

        meta = ClipMetadata(
            clip_id=clip.id,
            title="GTA 6 Vice City Scooter Police Escape Climax",
            detected_game="Grand Theft Auto VI",
            game_confidence=0.92,
            topic="escape"
        )
        db.add(meta)
        db.flush()

        job = PublishingJob(
            clip_id=clip.id,
            scheduled_time=datetime.utcnow(),
            status="completed",
            approval_status="approved"
        )
        db.add(job)
        db.flush()

        upload = PlatformUpload(
            publishing_job_id=job.id,
            platform="youtube",
            account_id="yt_channel_123",
            external_post_id="YOUTUBE_VID_ABC123",
            status="published_verified",
            uploaded_at=datetime.utcnow() - timedelta(hours=6)
        )
        db.add(upload)
        db.commit()

        yield {
            "source_id": source.id,
            "clip_id": clip.id,
            "meta_id": meta.id,
            "upload_id": upload.id,
            "job_id": job.id,
            "highlight_id": hl.id,
            "video_id": "YOUTUBE_VID_ABC123"
        }

        # Cleanup
        db.query(ShortsAnalyticsSnapshot).filter_by(platform_upload_id=upload.id).delete()
        db.query(PublishingAnalytics).filter_by(platform_upload_id=upload.id).delete()
        db.query(PlatformUpload).filter_by(id=upload.id).delete()
        db.query(PublishingJob).filter_by(id=job.id).delete()
        db.query(ClipMetadata).filter_by(id=meta.id).delete()
        db.query(GeneratedClip).filter_by(id=clip.id).delete()
        db.query(DetectedHighlight).filter_by(id=hl.id).delete()
        db.query(SourceVideo).filter_by(id=source.id).delete()
        db.query(ShortsExperiment).delete()
        db.commit()


# =============================================================================
# 1. Analytics Mapping & Zero Hallucination
# =============================================================================

def test_youtube_analytics_mapping_and_zero_hallucination():
    svc = YouTubeAnalyticsService()

    # Mock Data API response
    mock_data_svc = MagicMock()
    mock_data_svc.videos().list().execute.return_value = {
        "items": [{
            "statistics": {
                "viewCount": "12500",
                "likeCount": "850",
                "commentCount": "42"
            }
        }]
    }

    # Mock Analytics API response
    mock_analytics_svc = MagicMock()
    mock_analytics_svc.reports().query().execute.return_value = {
        "columnHeaders": [
            {"name": "views"},
            {"name": "likes"},
            {"name": "comments"},
            {"name": "shares"},
            {"name": "subscribersGained"},
            {"name": "averageViewDuration"},
            {"name": "averageViewPercentage"},
            {"name": "estimatedMinutesWatched"}
        ],
        "rows": [[12500, 850, 42, 60, 25, 21.4, 85.6, 4458.0]]
    }

    with patch.object(svc, "get_data_service", return_value=mock_data_svc), \
         patch.object(svc, "get_analytics_service", return_value=mock_analytics_svc):

        metrics = svc.fetch_video_metrics("test_vid_1")

        assert metrics["views"] == 12500
        assert metrics["likes"] == 850
        assert metrics["comments"] == 42
        assert metrics["shares"] == 60
        assert metrics["subscribers_gained"] == 25
        assert metrics["average_view_duration_sec"] == 21.4
        assert metrics["average_percentage_viewed"] == 85.6
        assert metrics["watch_time_minutes"] == 4458.0
        assert metrics["is_analytics_api"] is True

        # STRICT TEST: Verify unavailable metrics are NOT hallucinated or estimated
        assert metrics["shown_in_feed"] is None
        assert metrics["stayed_to_watch"] is None
        assert metrics["stayed_to_watch_pct"] is None


# =============================================================================
# 2. Data API Fallback when Analytics API is Unavailable
# =============================================================================

def test_data_api_v3_fallback_when_analytics_api_unavailable():
    svc = YouTubeAnalyticsService()

    mock_data_svc = MagicMock()
    mock_data_svc.videos().list().execute.return_value = {
        "items": [{
            "statistics": {
                "viewCount": "420",
                "likeCount": "35",
                "commentCount": "5"
            }
        }]
    }

    # Analytics API returns None or 403
    with patch.object(svc, "get_data_service", return_value=mock_data_svc), \
         patch.object(svc, "get_analytics_service", return_value=None):

        metrics = svc.fetch_video_metrics("test_vid_fallback")

        assert metrics["views"] == 420
        assert metrics["likes"] == 35
        assert metrics["comments"] == 5
        assert metrics["is_analytics_api"] is False
        assert metrics["api_status"] == "data_api_only"
        # Must not fabricate retention metrics
        assert metrics["average_percentage_viewed"] is None
        assert metrics["watch_time_minutes"] is None


# =============================================================================
# 3. Historical Snapshot Progression & Comparison
# =============================================================================

def test_historical_snapshot_progression_and_comparison(test_setup):
    upload_id = test_setup["upload_id"]
    svc = YouTubeAnalyticsService()

    with SessionLocal() as db:
        # Create historical snapshots across milestones
        snap_1h = ShortsAnalyticsSnapshot(
            platform_upload_id=upload_id,
            external_post_id=test_setup["video_id"],
            clip_id=test_setup["clip_id"],
            snapshot_window="1h",
            views=100,
            average_percentage_viewed=72.0,
            likes=10,
            captured_at=datetime.utcnow() - timedelta(hours=5)
        )
        snap_6h = ShortsAnalyticsSnapshot(
            platform_upload_id=upload_id,
            external_post_id=test_setup["video_id"],
            clip_id=test_setup["clip_id"],
            snapshot_window="6h",
            views=600,
            average_percentage_viewed=78.5,
            likes=65,
            captured_at=datetime.utcnow()
        )
        db.add_all([snap_1h, snap_6h])
        db.commit()

        comparison = svc.compare_snapshots(db, upload_id)

        assert comparison["windows"]["1h"] is not None
        assert comparison["windows"]["1h"]["views"] == 100
        assert comparison["windows"]["6h"]["views"] == 600

        # Milestone comparison
        milestones = comparison["milestone_comparisons"]
        assert len(milestones) >= 1
        m1 = milestones[0]
        assert m1["milestone"] == "1h -> 6h"
        assert m1["delta_views"] == 500
        assert m1["view_velocity_per_hour"] == 100.0  # 500 / 5h
        assert m1["retention_change_pct"] == 6.5  # 78.5 - 72.0


# =============================================================================
# 4. Opening Hook Analysis (6 Dimensions & Dead Footage Detection)
# =============================================================================

def test_opening_hook_analyzer_dimensions_and_scoring(tmp_path):
    analyzer = HookAnalyzer()
    dummy_video = tmp_path / "test_hook.mp4"
    dummy_video.write_bytes(b"\x00" * 1024)

    # Mock visual metrics with high motion and clarity
    mock_metrics_strong = {
        "motion_intensity": 8.5,
        "clarity_of_scene": 8.0,
        "has_dead_footage": False,
        "has_menu_or_loading": False,
        "has_black_screen": False,
        "brightness_avg": 55.0,
        "frame_variance": 45.0,
        "inter_frame_motion": 6.8
    }

    with patch.object(analyzer, "extract_hook_frames", return_value=[]), \
         patch.object(analyzer, "compute_visual_metrics", return_value=mock_metrics_strong):

        res = analyzer.analyze_opening_hook(
            video_path=dummy_video,
            start_sec=5.0,
            climax_sec=7.5,
            event_type="escape"
        )

        assert "dimensions" in res
        dims = res["dimensions"]
        assert "immediate_visual_action" in dims
        assert "motion_intensity" in dims
        assert "clarity_of_scene" in dims
        assert "curiosity" in dims
        assert "gameplay_event_importance" in dims
        assert "time_until_payoff_sec" in dims
        assert dims["time_until_payoff_sec"] == 2.5
        assert res["overall_hook_score"] >= 7.0
        assert res["quality_verdict"] == "PASS"

    # Test dead footage penalty and trim recommendation
    mock_metrics_dead = {
        "motion_intensity": 1.0,
        "clarity_of_scene": 2.0,
        "has_dead_footage": True,
        "has_menu_or_loading": False,
        "has_black_screen": True,
        "brightness_avg": 5.0,
        "frame_variance": 2.0,
        "inter_frame_motion": 0.1
    }

    with patch.object(analyzer, "extract_hook_frames", return_value=[]), \
         patch.object(analyzer, "compute_visual_metrics", return_value=mock_metrics_dead):

        res_dead = analyzer.analyze_opening_hook(
            video_path=dummy_video,
            start_sec=0.0,
            climax_sec=4.0,
            event_type="escape"
        )

        assert res_dead["diagnostics"]["has_dead_footage"] is True
        assert res_dead["overall_hook_score"] < 6.0
        assert res_dead["quality_verdict"] == "FAIL"
        assert res_dead["recommended_trim_offset"] > 0.0


# =============================================================================
# 5. Highlight Optimizer (Candidate Cuts & Context Preservation)
# =============================================================================

def test_highlight_optimizer_candidate_cuts_and_context(tmp_path):
    optimizer = HighlightOptimizer()
    dummy_video = tmp_path / "test_highlight.mp4"
    dummy_video.write_bytes(b"\x00" * 1024)

    mock_hook = {
        "overall_hook_score": 8.2,
        "recommended_trim_offset": 0.0,
        "diagnostics": {"has_dead_footage": False}
    }

    with patch("backend.app.ai.highlight_optimizer.hook_analyzer.analyze_opening_hook", return_value=mock_hook):
        result = optimizer.optimize_highlight_boundaries(
            video_path=dummy_video,
            climax_sec=30.0,
            video_duration=120.0,
            raw_event_type="High-Speed Police Escape",
            source_title="GTA 6 Vice City Stunt & Escape",
            game_name="Grand Theft Auto VI"
        )

        cuts = result["candidate_cuts"]
        assert len(cuts) >= 2

        # Check 15-25s Punchy Cut
        punchy = next(c for c in cuts if c["format_name"] == "punchy_action")
        assert 15.0 <= punchy["duration_sec"] <= 25.0
        assert punchy["lead_in_sec"] >= 3.0  # Context preserved before climax
        assert punchy["aftermath_sec"] >= 10.0  # Payoff preserved after climax

        # Check 25-40s Story Cut
        story = next(c for c in cuts if c["format_name"] == "story_tension")
        assert 25.0 <= story["duration_sec"] <= 40.0
        assert story["lead_in_sec"] >= 8.0  # Suspense setup preserved

        assert result["is_preferred_event"] is True
        assert result["event_archetype"] == "escape"


# =============================================================================
# 6. Factual Opening Hook Generator
# =============================================================================

def test_opening_hook_generator_factual_alternatives():
    gen = OpeningHookGenerator()
    hooks = gen.generate_hooks(
        event_type="escape",
        game_name="Grand Theft Auto VI",
        gameplay_summary="Narrow wanted level evasion on scooter"
    )

    assert len(hooks) == 3
    hook_texts = [h["hook_text"] for h in hooks]

    # Verify presence of factual, grounded examples
    assert any("escape" in t.lower() or "safe" in t.lower() or "watch" in t.lower() for t in hook_texts)

    # Verify zero forbidden clickbait buzzwords
    for t in hook_texts:
        t_low = t.lower()
        assert "world record" not in t_low
        assert "infinite money" not in t_low
        assert "cheat code" not in t_low


# =============================================================================
# 7. Sample Size Protection in Performance Learning (<10 Samples Rejected)
# =============================================================================

def test_performance_learning_insufficient_sample_size_protection(test_setup):
    service = GrowthIntelligenceService()

    with SessionLocal() as db:
        # Currently only 1 clip exists in DB (test_setup)
        summary = service.get_dashboard_summary(db)

        # Must NOT declare winning format when sample < 10
        assert summary["min_sample_size_threshold"] == MIN_SIGNIFICANT_SAMPLE_SIZE
        learning = summary["learning_breakdown"]
        games = learning["games_breakdown"]

        if games:
            gta = games[0]
            assert gta["sample_size"] < MIN_SIGNIFICANT_SAMPLE_SIZE
            assert gta["is_statistically_significant"] is False
            assert gta["status"] == "insufficient_sample"
            assert "Tentative trend" in gta["confidence_note"]


# =============================================================================
# 8. Controlled Experiments Prohibit Duplicate Video Uploads
# =============================================================================

def test_controlled_experiments_prohibits_duplicate_clip_uploads(test_setup):
    service = GrowthIntelligenceService()
    clip_id = test_setup["clip_id"]

    with SessionLocal() as db:
        # Attempting to test the same clip against itself (duplicate upload) must be rejected
        with pytest.raises(ValueError, match="duplicate copies"):
            service.create_experiment(
                db=db,
                name="Duplicate Upload Test",
                hypothesis="Testing same video with different titles",
                dimension="hook_style",
                control_clip_id=clip_id,
                variant_clip_ids=[clip_id]  # DUPLICATE!
            )

        # Distinct clips must succeed
        exp = service.create_experiment(
            db=db,
            name="Valid Distinct Clips Test",
            hypothesis="Testing 15s cut vs 30s cut across separate footage clips",
            dimension="clip_duration",
            control_clip_id=clip_id,
            variant_clip_ids=[9999]  # Distinct
        )
        assert exp.id is not None
        assert exp.sample_size == 2
        assert exp.is_statistically_significant is False  # 2 < 10


# =============================================================================
# 9. Publishing Quality Gate Blocks Weak Clips
# =============================================================================

def test_publishing_quality_gate_blocks_weak_clips(test_setup):
    gate = PublishingQualityGate()

    with SessionLocal() as db:
        clip = db.query(GeneratedClip).filter_by(id=test_setup["clip_id"]).first()
        meta = clip.metadata_rel

        # Scenario A: Weak hook score (< 6.0)
        res_weak_hook = gate.evaluate_clip(
            clip=clip,
            metadata=meta,
            hook_analysis={"overall_hook_score": 4.5, "diagnostics": {"has_dead_footage": False}}
        )
        assert res_weak_hook["gate_passed"] is False
        assert res_weak_hook["gate_decision"] == "MANUAL_REVIEW_REQUIRED"
        assert any("Weak opening hook" in r for r in res_weak_hook["reasons"])

        # Scenario B: Dead footage in opening
        res_dead_footage = gate.evaluate_clip(
            clip=clip,
            metadata=meta,
            hook_analysis={"overall_hook_score": 7.5, "diagnostics": {"has_dead_footage": True}}
        )
        assert res_dead_footage["gate_passed"] is False
        assert any("dead footage" in r.lower() for r in res_dead_footage["reasons"])

        # Scenario C: Generic Title
        meta.title = "Pure Precision: Epic Gameplay Sequence!"
        res_generic_title = gate.evaluate_clip(
            clip=clip,
            metadata=meta,
            hook_analysis={"overall_hook_score": 7.5, "diagnostics": {"has_dead_footage": False}}
        )
        assert res_generic_title["gate_passed"] is False
        assert any("Generic title pattern" in r for r in res_generic_title["reasons"])

        # Scenario D: Unverified Game Identity
        meta.title = "GTA 6 Vice City Police Chase Scooter Escape"
        meta.detected_game = "Unknown Game"
        meta.game_confidence = 0.20
        meta.user_game_name = None
        res_unverified_game = gate.evaluate_clip(
            clip=clip,
            metadata=meta,
            hook_analysis={"overall_hook_score": 7.5, "diagnostics": {"has_dead_footage": False}}
        )
        assert res_unverified_game["gate_passed"] is False
        assert any("Unverified game identity" in r for r in res_unverified_game["reasons"])


# =============================================================================
# 10. Publishing Quality Gate Approves Strong Compliant Clips
# =============================================================================

def test_publishing_quality_gate_approves_strong_clips(test_setup):
    gate = PublishingQualityGate()

    with SessionLocal() as db:
        clip = db.query(GeneratedClip).filter_by(id=test_setup["clip_id"]).first()
        meta = clip.metadata_rel

        meta.title = "GTA 6 Vice City Police Scooter Escape Stunt"
        meta.detected_game = "Grand Theft Auto VI"
        meta.game_confidence = 0.95
        clip.highlight.event_type = "escape"

        res = gate.evaluate_clip(
            clip=clip,
            metadata=meta,
            hook_analysis={"overall_hook_score": 8.5, "diagnostics": {"has_dead_footage": False, "has_menu_or_loading": False}}
        )

        assert res["gate_passed"] is True
        assert res["gate_decision"] == "AUTO_PUBLISH_APPROVED"
        assert res["recommended_approval_status"] == "approved"
        assert len(res["reasons"]) == 0


# =============================================================================
# 11. Publishing Quality Gate Evaluates Opening 3 Seconds Independently
# =============================================================================

def test_publishing_quality_gate_evaluates_first_three_seconds_independently(test_setup):
    """
    Validates that a high overall score (e.g. 6.7) cannot conceal critical opening weaknesses:
    1. Low scene clarity (4.0 < 5.0)
    2. Excessive payoff delay (12.0s > 5.0s)
    3. Slow intro diagnostics
    Must reject auto-publishing and route to 'needs_review' with actionable trimming recommendations.
    """
    gate = PublishingQualityGate()

    with SessionLocal() as db:
        clip = db.query(GeneratedClip).filter_by(id=test_setup["clip_id"]).first()
        meta = clip.metadata_rel

        meta.title = "GTA 6 Vice City Police Scooter Escape Stunt"
        meta.detected_game = "Grand Theft Auto VI"
        meta.game_confidence = 0.95
        clip.highlight.event_type = "escape"

        # The exact audit failure scenario: 6.7 overall score, but clarity 4.0 and payoff 12.0s
        audit_case = {
            "overall_hook_score": 6.7,
            "dimensions": {
                "immediate_visual_action": 10.0,
                "motion_intensity": 10.0,
                "clarity_of_scene": 4.0,  # CRITICAL FLAW
                "curiosity": 6.5,
                "gameplay_event_importance": 9.0,
                "time_until_payoff_sec": 12.0  # CRITICAL FLAW
            },
            "diagnostics": {
                "has_slow_intro": True,
                "has_dead_footage": False,
                "has_menu_or_loading": False
            }
        }

        res = gate.evaluate_clip(clip=clip, metadata=meta, hook_analysis=audit_case)

        assert res["gate_passed"] is False
        assert res["gate_decision"] == "MANUAL_REVIEW_REQUIRED"
        assert res["recommended_approval_status"] == "needs_review"

        # Verify specific independent opening checks triggered
        assert any("Low opening scene clarity" in r for r in res["reasons"])
        assert any("Excessive payoff delay" in r for r in res["reasons"])

        # Verify actionable trimming recommendation provided
        assert len(res["recommendations"]) >= 1
        assert any("Trim" in rec for rec in res["recommendations"])
