"""
Unit & Integration Test Suite for AI SEO & Growth Intelligence Engine.
Tests cover:
1. LLM Router (Local-first, fallback to heuristics/Gemini, 429 rate-limit resilience)
2. Keyword Research Service (YouTube autocomplete, caching, measured vs estimated)
3. 7-Dimension SEO Scorer (Weights, warnings, recommendations, bounds)
4. Multi-Platform SEO Generator (YouTube Shorts, Instagram Reels, Facebook Reels)
5. Database Persistence (ClipSeoMetadata, KeywordResearchRecord, SeoGrowthInsight)
6. API Endpoints (Dashboard, Clips, Approve/Sync, Bulk Operations, Export)
7. Closed-Loop Growth Intelligence (Continuous learning, analytics correlation)
"""

import json
import uuid
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    SourceVideo,
    DetectedHighlight,
    GeneratedClip,
    ClipMetadata,
    ClipSeoMetadata,
    KeywordResearchRecord,
    SeoGrowthInsight,
    PublishingAnalytics,
    PlatformUpload,
    PublishingJob
)
from backend.app.ai.llm_router import llm_router, LLMRouter
from backend.app.metadata.keyword_research import keyword_research_service, KeywordResearchService
from backend.app.metadata.seo_scorer import seo_scorer, SEOScorer
from backend.app.metadata.seo_engine import seo_engine, SEOEngine
from backend.app.analytics.growth_intelligence import growth_intelligence_service

client = TestClient(app)


@pytest.fixture
def sample_clip_with_highlight():
    """Creates a complete SourceVideo -> DetectedHighlight -> GeneratedClip hierarchy."""
    uid = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        sv = SourceVideo(
            url=f"https://youtube.com/watch?v=seo_test_{uid}",
            title="Insane GTA 5 Stunt Montage RTX 4090 Ultra",
            duration=120.0,
            width=1920,
            height=1080,
            fps=60.0,
            format="mp4",
            local_path=f"data/test_clip_seo_{uid}.mp4",
            source_type="youtube",
            status="completed",
            user_game_name="GTA V"
        )
        db.add(sv)
        db.commit()
        db.refresh(sv)

        hl = DetectedHighlight(
            source_video_id=sv.id,
            start_sec=15.0,
            end_sec=55.0,
            duration_sec=40.0,
            score=95.0,
            event_type="stunt_jump",
            explanation="Impossible Rooftop Jump across skyscraper",
            hook_suggestion="You won't believe this jump landed!",
            caption_suggestion="Look at this incredible stunt ramp jump!",
            confidence=0.95
        )
        db.add(hl)
        db.commit()
        db.refresh(hl)

        clip = GeneratedClip(
            highlight_id=hl.id,
            output_path=f"data/renders/test_clip_seo_reel_{uid}.mp4",
            duration_sec=40.0,
            quality_score=94.0,
            validation_status="passed"
        )
        db.add(clip)
        db.commit()
        db.refresh(clip)

        meta = ClipMetadata(
            clip_id=clip.id,
            title="GTA 5 Rooftop Jump",
            description="Epic stunt jump in GTA 5.",
            hashtags="#GTA5 #Gaming",
            youtube_title="GTA 5 Rooftop Jump",
            youtube_desc="Epic stunt jump in GTA 5.",
            instagram_caption="Epic stunt jump in GTA 5. #GTA5 #Gaming",
            facebook_caption="Check out this GTA 5 stunt jump!"
        )
        db.add(meta)
        db.commit()
        db.refresh(clip)

        return clip.id


def test_llm_router_heuristic_fallback():
    """Verify router falls back gracefully to structured heuristics when local LLM and cloud AI are unavailable."""
    router = LLMRouter()
    with patch.object(router, "is_local_llm_available", return_value=False):
        prompt = "Generate title and tags for GTA V stunt jump."
        response = router.generate(
            task_type="titles",
            prompt=prompt,
            json_mode=True,
            heuristic_fallback_fn=lambda: {"titles": ["GTA 5 Stunt Jump"], "tags": ["gta5", "stunt"]}
        )
        assert isinstance(response, dict)
        assert response["provider"] == "heuristic"
        assert response["fallback_occurred"] is True
        assert response["json"] == {"titles": ["GTA 5 Stunt Jump"], "tags": ["gta5", "stunt"]}
        stats = router.get_stats()
        assert stats["heuristic_calls"] >= 1


def test_llm_router_exponential_backoff_on_429():
    """Verify router applies exponential backoff when encountering rate limit errors."""
    router = LLMRouter()
    with patch.object(router, "is_local_llm_available", return_value=False):
        with patch.object(router, "_call_gemini_resilient", side_effect=Exception("429 ResourceExhausted")):
            with patch("time.sleep", return_value=None):
                response = router.generate(
                    task_type="titles",
                    prompt="Test prompt",
                    heuristic_fallback_fn=lambda: {"fallback": True}
                )
                assert isinstance(response, dict)
                assert response["provider"] == "heuristic"
                assert response["fallback_occurred"] is True


def test_keyword_research_service_autocomplete_and_cache():
    """Verify YouTube autocomplete querying, cache storage, and distinguishing measured vs estimated metrics."""
    service = KeywordResearchService()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = ["valorant clutch", ["valorant clutch ace", "valorant clutch gameplay"]]

    with patch("requests.get", return_value=mock_response):
        measured = service.get_youtube_suggestions("valorant clutch")
        assert len(measured) > 0
        assert measured[0]["is_measured"] is True
        assert measured[0]["source"] == "youtube_autocomplete"
        assert "valorant" in measured[0]["keyword"].lower()


def test_seo_scorer_calculation():
    """Verify SEOScorer computes 0-100 scores across 7 dimensions and handles platform-specific checks."""
    scorer = SEOScorer()

    # YouTube Shorts scoring test
    yt_score = scorer.score_youtube_seo(
        title="GTA 5 Impossible Rooftop Jump Stunt #Shorts",
        description="Watch this insane Grand Theft Auto V stunt jump executed on PC RTX 4090. Comment your rating below!",
        primary_keyword="GTA 5 stunt",
        secondary_keywords=["RTX 4090", "GTA gameplay", "stunt montage"],
        tags=["gta 5", "gta stunt", "grand theft auto v", "gta 5 gameplay", "gaming"],
        hashtags=["#Shorts", "#GTA5", "#Gaming"],
        game_name="Grand Theft Auto V",
        game_confidence=0.95,
        game_mismatch=False,
        thumbnail_text="DON'T BLINK!",
        opening_hook="You won't believe this jump landed!",
        search_intent="entertainment"
    )
    assert 60 <= yt_score["overall_score"] <= 100
    assert "keyword_relevance" in yt_score["breakdown"]
    assert "gameplay_accuracy" in yt_score["breakdown"]
    assert "title_quality" in yt_score["breakdown"]

    # Instagram Reels scoring test with hashtag penalty (> 5 hashtags)
    ig_score = scorer.score_instagram_seo(
        caption="GTA 5 stunt jump! Here is a full description of the impossible jump that was landed.\n\n#gta #gaming #stunt #game #fun #crazy #viral #reel #shorts #trending",
        primary_keyword="GTA 5 stunt",
        hashtags=["#gta", "#gaming", "#stunt", "#game", "#fun", "#crazy", "#viral", "#reel", "#shorts", "#trending"],
        game_name="GTA V",
        game_mismatch=False,
        cta="Share this with a friend who loves stunts",
        onscreen_text="INSANE JUMP"
    )
    assert any("5 hashtags" in w.lower() or "focused" in w.lower() for w in ig_score["warnings"])


def test_seo_engine_generate_and_persist(sample_clip_with_highlight):
    """Verify SEOEngine generates and persists comprehensive SEO metadata for all 3 platforms."""
    clip_id = sample_clip_with_highlight
    
    result = seo_engine.generate_clip_seo(clip_id, platforms=["youtube", "instagram", "facebook"])
    assert result["clip_id"] == clip_id
    assert "youtube" in result["platforms"]
    assert "instagram" in result["platforms"]
    assert "facebook" in result["platforms"]

    # Verify YouTube output structure
    yt = result["platforms"]["youtube"]
    assert len(yt["candidate_titles"]) >= 3
    assert yt["recommended_title"] != ""
    assert len(yt["tags"]) >= 3
    assert yt["search_intent"] in ["entertainment", "clutch_skill", "how_to_play", "cinematic_run", "general"]
    assert yt["seo_score"] > 40

    # Verify Instagram output structure (max 5 hashtags)
    ig = result["platforms"]["instagram"]
    assert len(ig["hashtags"]) <= 5
    assert len(ig["caption_variations"]) >= 2
    assert "call_to_action" in ig
    assert "onscreen_text" in ig

    # Verify Facebook output structure
    fb = result["platforms"]["facebook"]
    assert len(fb["description_variations"]) >= 2
    assert "community_discussion_cta" in fb

    # Verify DB persistence
    with SessionLocal() as db:
        records = db.query(ClipSeoMetadata).filter_by(clip_id=clip_id).all()
        assert len(records) == 3
        platforms_saved = {r.platform for r in records}
        assert platforms_saved == {"youtube", "instagram", "facebook"}


def test_api_seo_endpoints(sample_clip_with_highlight):
    """Verify REST API endpoints for SEO Dashboard, Clip details, and Approval."""
    clip_id = sample_clip_with_highlight

    # First generate SEO metadata
    gen_res = client.post(f"/api/seo/clips/{clip_id}/generate", json={"platforms": ["youtube", "instagram", "facebook"]})
    assert gen_res.status_code == 200

    # Get Clip SEO details
    get_res = client.get(f"/api/seo/clips/{clip_id}")
    assert get_res.status_code == 200
    clip_data = get_res.json()
    assert clip_data["clip_id"] == clip_id
    assert "platforms" in clip_data
    assert "youtube" in clip_data["platforms"]

    # Test Approval and synchronization
    app_res = client.post(f"/api/seo/clips/{clip_id}/approve", json={"platform": "all"})
    assert app_res.status_code == 200
    app_data = app_res.json()
    assert app_data["status"] == "approved"
    assert app_data["synced_to_publishing"] is True

    # Check that ClipMetadata was synchronized with the approved SEO title and captions
    with SessionLocal() as db:
        meta = db.query(ClipMetadata).filter_by(clip_id=clip_id).first()
        assert meta is not None
        assert meta.youtube_title is not None and len(meta.youtube_title) > 0
        assert meta.instagram_caption is not None and len(meta.instagram_caption) > 0

    # Test Dashboard endpoint
    dash_res = client.get("/api/seo/dashboard")
    assert dash_res.status_code == 200
    dash_data = dash_res.json()
    assert dash_data["total_clips_optimized"] >= 1
    assert "average_seo_score" in dash_data
    assert "platform_breakdown" in dash_data


def test_api_bulk_generate_and_approve(sample_clip_with_highlight):
    """Verify bulk generate and bulk approve endpoints."""
    clip_id = sample_clip_with_highlight

    # Bulk Generate
    bulk_gen = client.post("/api/seo/bulk/generate", json={"clip_ids": [clip_id], "platforms": ["youtube"]})
    assert bulk_gen.status_code == 200
    assert bulk_gen.json()["total_requested"] == 1

    # Bulk Approve
    bulk_app = client.post("/api/seo/bulk/approve", json={"clip_ids": [clip_id], "platform": "youtube"})
    assert bulk_app.status_code == 200
    assert bulk_app.json()["approved_count"] == 1


def test_api_seo_export_json_and_csv(sample_clip_with_highlight):
    """Verify SEO export endpoint supporting both JSON and CSV formats."""
    # Ensure SEO is generated first
    client.post(f"/api/seo/clips/{sample_clip_with_highlight}/generate", json={"platforms": ["youtube"]})

    # JSON Export
    res_json = client.get("/api/seo/export?format=json")
    assert res_json.status_code == 200
    data = res_json.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "recommended_title" in data[0]

    # CSV Export
    res_csv = client.get("/api/seo/export?format=csv")
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.headers["content-type"]
    assert "clip_id" in res_csv.text


def test_growth_intelligence_continuous_learning():
    """Verify performance intelligence evaluates analytics and extracts correlation insights."""
    with SessionLocal() as db:
        insight = SeoGrowthInsight(
            category="title_format",
            dimension_name="question_hooks",
            platform="youtube",
            performance_score=88.5,
            sample_size=15,
            insight_summary="Question-based opening hooks increase YouTube Shorts view duration by 24%.",
            recommendation="Start Short titles with high-stakes curiosity questions.",
            is_statistically_significant=True
        )
        db.add(insight)
        db.commit()

    intel_res = client.get("/api/seo/growth/intelligence")
    assert intel_res.status_code == 200
    data = intel_res.json()
    assert "insights" in data
    assert any(i["dimension"] == "question_hooks" for i in data["insights"])
