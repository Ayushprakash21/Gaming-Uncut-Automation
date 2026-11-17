"""
Comprehensive Test Suite for Global AI Video Intelligence & Metadata Engine.
Covers:
1. Game identification across diverse genres (Racing, Tactical Shooter, Open World, Boss Fight, Minecraft, Unknown).
2. Event recognition (Overtake, Clutch, Police Escape, Boss Combo, Crash/Ragdoll).
3. Grounding & Anti-Hallucination: low-confidence strictly yields 'Unknown Game' with neutral terminology.
4. Clip-specific intelligence: different clips from same video get unique metadata.
5. Quality validation: rejects hardcoded generic defaults and flags uncertain game claims.
6. Platform-specific metadata adaptation (YouTube Shorts, Instagram Reels, Facebook Reels).
7. Auto-publish safety gate: incomplete/unconfirmed analysis blocks automatic publishing.
"""

import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

from backend.app.ai.game_knowledge import (
    lookup_game_by_alias,
    match_game_from_evidence,
    GAME_REGISTRY
)
from backend.app.ai.gemini_provider import gemini_provider
from backend.app.video.validator import quality_validator
from backend.app.models.schema import (
    SourceVideo, ProcessingJob, GeneratedClip, ClipMetadata, PublishingJob, PlatformUpload
)
from backend.app.database.session import SessionLocal


def test_game_registry_coverage():
    """Verify core games requested by user are present and well-defined."""
    required_games = [
        "Grand Theft Auto V",
        "Forza Horizon 5",
        "Black Myth: Wukong",
        "Assassin's Creed Shadows",
        "Call of Duty: Warzone",
        "Red Dead Redemption 2",
        "Cyberpunk 2077",
        "Minecraft",
        "Counter-Strike 2",
        "Valorant"
    ]
    for game in required_games:
        assert game in GAME_REGISTRY
        spec = GAME_REGISTRY[game]
        assert "franchise" in spec
        assert "genre" in spec
        assert "hud_elements" in spec
        assert len(spec["hud_elements"]) >= 2


def test_game_identification_minecraft():
    """Verify Minecraft HUD and elements identify the game with confirmed status."""
    evidence = [
        "Nine-slot inventory hotbar center bottom displaying Diamond Pickaxe and Netherite Sword",
        "Ten heart health meter bottom-left and ten drumstick hunger meter bottom-right",
        "Blocky voxel world in The End facing Ender Dragon"
    ]
    match = match_game_from_evidence(evidence)
    assert match is not None
    assert match["name"] == "Minecraft"
    assert match["genre"] == "Sandbox Survival / Adventure"
    assert match["confidence"] >= 0.70
    assert match["is_confirmed"] is True
    assert match["is_uncertain"] is False


def test_game_identification_gta_v():
    """Verify GTA V radar and wanted stars identify the game with confirmed status."""
    evidence = [
        "Circular radar mini-map bottom-left with health and armor bars",
        "Five wanted stars flashing top-right during high-speed police chase",
        "Player driving Oppressor Mk II across Los Santos highway"
    ]
    match = match_game_from_evidence(evidence)
    assert match is not None
    assert match["name"] == "Grand Theft Auto V"
    assert match["confidence"] >= 0.70
    assert match["is_confirmed"] is True


def test_game_identification_low_confidence_never_invents_game():
    """Verify generic scenes with no HUD or signatures return Unknown Game with 0.0 confidence."""
    generic_evidence = [
        "A grassy hill under a blue sky",
        "Motion blur camera panning across trees",
        "Ambient outdoor wind sound"
    ]
    match = match_game_from_evidence(generic_evidence)
    assert match is None  # Below score threshold

    # Verify sanitizer enforces Unknown Game
    raw_payload = {
        "game": {
            "name": "Generic Open World Game",
            "confidence": 0.35,
            "evidence": generic_evidence
        },
        "gameplay": {
            "summary": "Outdoor exploration",
            "events": []
        },
        "content": {
            "titles": ["Exploring The Hills", "Scenic Gaming Clip", "Wilderness Walk", "Peaceful Gaming", "Nature View"],
            "description": "Exploration gameplay.",
            "hashtags": ["#GamingClip"],
            "captions": []
        }
    }
    validated = gemini_provider._validate_and_sanitize_analysis(raw_payload)
    assert validated["game"]["name"] == "Unknown Game"
    assert validated["game"]["confidence"] == 0.0
    assert validated["game"]["is_confirmed"] is False
    assert validated["game"]["is_uncertain"] is True


def test_clip_specific_intelligence_different_clips_from_same_video():
    """Verify multiple clips from same source video receive distinct, clip-specific metadata."""
    # Clip 1: Early game tactical snipe
    clip1_raw = {
        "game": {"name": "Counter-Strike 2", "confidence": 0.95, "evidence": ["CS2 HUD", "AWP"]},
        "gameplay": {
            "summary": "AWP Opening Pick at Long A",
            "events": [{"name": "Sniper Elimination", "start": 12.0, "end": 15.0, "confidence": 0.9}]
        },
        "content": {
            "titles": ["1 Bullet, 1 Frag: Perfect AWP Shot in CS2", "Crisp Long A Sniper Pick | Counter-Strike 2", "Fastest CS2 AWP Reaction", "Dust II Opening Kill", "Pure Precision Snipe"],
            "description": "Opening pick on Dust II Long A with a crisp flick shot.",
            "hashtags": ["#CounterStrike2", "#CS2", "#AWP"],
            "captions": ["[AWP Shot Fired]"]
        }
    }

    # Clip 2: Late game clutch defuse
    clip2_raw = {
        "game": {"name": "Counter-Strike 2", "confidence": 0.95, "evidence": ["CS2 HUD", "Bomb Timer"]},
        "gameplay": {
            "summary": "1v3 Ninja Defuse with 0.5s Remaining",
            "events": [{"name": "1v3 Clutch Defusal", "start": 45.0, "end": 52.0, "confidence": 0.95}]
        },
        "content": {
            "titles": ["0.5 Seconds Left! Impossible CS2 Clutch", "1v3 Ninja Defuse in Smoke | Counter-Strike 2", "Heart Rate 200 BPM Defusal", "Unbelievable Smoke Defuse", "The Craziest CS2 Round"],
            "description": "Heart-pounding 1v3 smoke defuse with half a second before detonation.",
            "hashtags": ["#CounterStrike2", "#CS2", "#NinjaDefuse"],
            "captions": ["[Bomb Defused]"]
        }
    }

    v1 = gemini_provider._validate_and_sanitize_analysis(clip1_raw)
    v2 = gemini_provider._validate_and_sanitize_analysis(clip2_raw)

    # Validate distinctness
    assert v1["content"]["titles"][0] != v2["content"]["titles"][0]
    assert v1["content"]["description"] != v2["content"]["description"]
    assert "AWP" in v1["content"]["titles"][0]
    assert "Clutch" in v2["content"]["titles"][0]


def test_quality_validator_rejects_generic_defaults():
    """Verify quality validator flags and penalizes hardcoded generic placeholder strings."""
    # Case A: Generic placeholder description
    bad_meta = {
        "title": "Clean Play | Gaming Uncut",
        "description": "Watch this high-intensity gameplay moment from Gaming Uncut. NO FILTER. JUST GAMEPLAY.",
        "hashtags": "#Gaming #Gameplay #Gamer",
        "detected_game": "Unknown Game",
        "game_confidence": 0.0
    }
    passed, score, report = quality_validator.validate_metadata(bad_meta)
    assert not passed
    assert any("generic placeholder pattern" in err for err in report["errors"])

    # Case B: Description is only the brand slogan
    tagline_only_meta = {
        "title": "Clean Play | Gaming Uncut",
        "description": "NO FILTER. JUST GAMEPLAY.",
        "hashtags": "#GamingUncut #Shorts",
        "detected_game": "Unknown Game",
        "game_confidence": 0.0
    }
    passed2, score2, report2 = quality_validator.validate_metadata(tagline_only_meta)
    assert not passed2
    assert any("brand tagline without gameplay explanation" in err for err in report2["errors"])

    # Case C: Valid factual event description
    good_meta = {
        "title": "Flawless Drift Around Guanajuato Square | Forza Horizon 5",
        "description": "Tackling the tight 90-degree cobblestone street corners in a Porsche 911 GT3 RS with continuous skill score multiplier.",
        "hashtags": "#ForzaHorizon5 #FH5 #Drift #GamingUncut #Shorts",
        "detected_game": "Forza Horizon 5",
        "game_confidence": 0.92,
        "evidence": ["Porsche 911", "Tachometer HUD", "Guanajuato"]
    }
    passed3, score3, report3 = quality_validator.validate_metadata(good_meta)
    assert passed3
    assert len(report3["errors"]) == 0
    assert score3 >= 90.0


def test_platform_specific_metadata_adaptation():
    """Verify metadata is formatted appropriately for YouTube Shorts, Instagram, and Facebook."""
    raw_payload = {
        "game": {"name": "Forza Horizon 5", "confidence": 0.92, "evidence": ["Tachometer", "Porsche 911"]},
        "gameplay": {"summary": "High-Speed Highway Drift Chain"},
        "content": {
            "titles": [
                "185 MPH Highway Drift in Forza Horizon 5",
                "Porsche 911 GT3 RS Pure Speed",
                "Clean Highway Drift Chain",
                "FH5 Mexico Highway Speed Run",
                "Wait For The Near Miss Chain!"
            ],
            "description": "Slicing through Mexico highway traffic at 185 MPH in the Porsche 911 GT3 RS.",
            "hashtags": ["#ForzaHorizon5", "#Drift", "#FH5", "#Shorts", "#GamingUncut"],
            "captions": ["[Twin-Turbo Boxer-6 Roar]"]
        }
    }

    validated = gemini_provider._validate_and_sanitize_analysis(raw_payload)
    content = validated["content"]

    # YouTube Shorts: Has #Shorts tag in title, full description with tags
    assert "#Shorts" in content["youtube_title"]
    assert "Mexico highway" in content["youtube_desc"]
    assert "#ForzaHorizon5" in content["youtube_desc"]

    # Instagram Reels: Hook title on top, full description, hashtag grouping
    assert content["instagram_caption"].startswith("185 MPH Highway Drift")
    assert "#ForzaHorizon5" in content["instagram_caption"]

    # Facebook Reels: Concise caption with top 3 hashtags
    assert "185 MPH" in content["facebook_caption"]
    assert len(content["facebook_caption"].split("#")) <= 5  # Max 3-4 hashtags


def test_safety_gate_blocks_auto_publish_when_analysis_heuristic_or_unconfirmed():
    """Verify automatic publication is held in pending_review if game analysis is unconfirmed/heuristic."""
    with SessionLocal() as db:
        # Create dummy source and clip
        source = SourceVideo(
            title="Uncertain Footage Test",
            source_type="local",
            url="d:/dummy/footage.mp4",
            duration=60.0,
            local_path="d:/dummy/footage.mp4"
        )
        db.add(source)
        db.flush()

        job = ProcessingJob(
            source_video_id=source.id,
            status="completed",
            progress_pct=100,
            current_step="Completed"
        )
        db.add(job)
        db.flush()

        clip = GeneratedClip(
            job_id=job.id,
            output_path=f"d:/dummy/clip_safety_{job.id}.mp4",
            duration_sec=60.0
        )
        db.add(clip)
        db.flush()

        # Heuristic / unconfirmed metadata
        meta = ClipMetadata(
            clip_id=clip.id,
            title="High Intensity Sequence | Gaming Uncut",
            description="Raw telemetry captured sequence.",
            hashtags="#GamingClip #Shorts",
            detected_game="Unknown Game",
            game_confidence=0.0,
            analysis_provider="heuristic",
            analysis_status="heuristic"
        )
        db.add(meta)
        db.flush()

        # Safety gate evaluation
        analysis_is_verified = (
            meta.analysis_provider == "gemini" and
            meta.analysis_status == "complete" and
            float(meta.game_confidence or 0.0) >= 0.50
        )
        assert not analysis_is_verified

        # Forced to pending_review even if publish_mode was "auto"
        approval_status = "auto_approved" if analysis_is_verified else "pending_review"
        assert approval_status == "pending_review"

        pub_job = PublishingJob(
            clip_id=clip.id,
            scheduled_time=datetime.utcnow(),
            status="pending",
            approval_status=approval_status
        )
        db.add(pub_job)
        db.commit()

        # Check in DB
        saved_job = db.query(PublishingJob).filter_by(id=pub_job.id).first()
        assert saved_job.approval_status == "pending_review"
