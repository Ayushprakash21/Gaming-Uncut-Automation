"""
Unit & Integration Tests for AI-Powered Game Recognition & Content Intelligence.
Tests:
1. Frame Extraction Pipeline (adaptive sampling around peaks, opening, climax).
2. Game Knowledge Layer (signatures, evidence matching, alias lookup).
3. Multimodal Analysis & Grounded Game Recognition (Forza vs COD vs Black Myth).
4. Prevention of generic titles (Racing never receives FPS metadata; Shooter never receives racing metadata).
5. Low-confidence handling (returns 'Unknown Game', never invents fake game names).
6. 5 Selectable Title Candidates generation.
7. Content Caching & SHA256 Fingerprinting.
8. Honest Heuristic Fallback behavior when Gemini is unconfigured or offline.
9. Database persistence & Re-analysis endpoint (/api/clips/{clip_id}/reanalyze).
"""

import json
import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.ai.game_knowledge import (
    lookup_game_by_alias,
    match_game_from_evidence,
    GAME_REGISTRY
)
from backend.app.ai.frame_extractor import extract_multimodal_frames
from backend.app.ai.analysis_cache import compute_video_fingerprint, get_cached_analysis, store_cached_analysis
from backend.app.ai.gemini_provider import gemini_provider, GeminiAIProvider
from backend.app.models.schema import SourceVideo, ProcessingJob, GeneratedClip, ClipMetadata
from backend.app.database.session import SessionLocal
from backend.app.security.vault import vault

client = TestClient(app)

@pytest.fixture
def synthetic_video(tmp_path):
    video = tmp_path / "gameplay_test.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
        "-t", "5",
        "-c:v", "libx264", "-c:a", "aac",
        str(video)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return video


# =========================================================================
# 1. Frame Extraction Engine Tests
# =========================================================================

def test_frame_extractor_adaptive_sampling(synthetic_video, tmp_path):
    """Verify frame extraction captures opening, action peaks, midpoint, and climax."""
    frames = extract_multimodal_frames(
        video_path=synthetic_video,
        output_dir=tmp_path / "extracted_frames",
        peak_timestamps=[2.5],
        max_frames=4
    )
    assert len(frames) >= 3, "Expected at least 3 sampled frames"
    roles = [f["role"] for f in frames]
    assert "opening" in roles
    assert "climax" in roles
    for f in frames:
        assert f["path"].exists()
        assert len(f["bytes"]) > 0
        assert f["timestamp"] >= 0.0


# =========================================================================
# 2. Game Knowledge Layer Tests
# =========================================================================

def test_game_knowledge_alias_lookup():
    """Verify alias lookup works for common abbreviations."""
    gta = lookup_game_by_alias("GTA 5")
    assert gta is not None
    assert gta["name"] == "Grand Theft Auto V"
    assert gta["franchise"] == "Grand Theft Auto"

    wukong = lookup_game_by_alias("Black Myth Wukong")
    assert wukong is not None
    assert wukong["name"] == "Black Myth: Wukong"
    assert "Staff" in wukong["key_weapons"]

    forza = lookup_game_by_alias("FH5")
    assert forza is not None
    assert forza["name"] == "Forza Horizon 5"
    assert forza["genre"] == "Sim-Cade Racing"


def test_game_knowledge_evidence_matching():
    """Verify evidence points (HUD, weapons, vehicles) identify the exact game."""
    # Racing evidence
    racing_evidence = [
        "Circular tachometer bottom-right displaying 185 MPH in 6th gear",
        "Player driving a Porsche 911 GT3 RS across Guanajuato roads",
        "Skill score popup indicating Drift and Near Miss chain"
    ]
    match = match_game_from_evidence(racing_evidence)
    assert match is not None
    assert match["name"] == "Forza Horizon 5"
    assert match["genre"] == "Sim-Cade Racing"
    assert match["confidence"] >= 0.70

    # Soulslike RPG evidence
    wukong_evidence = [
        "Destined One character performing acrobatic staff combo",
        "Ruyi Jingu Bang staff weapon with golden glow",
        "Gourd drinking counter bottom right"
    ]
    w_match = match_game_from_evidence(wukong_evidence)
    assert w_match is not None
    assert w_match["name"] == "Black Myth: Wukong"
    assert w_match["genre"] == "Action RPG / Soulslike"

    # Tactical FPS evidence
    cs_evidence = [
        "Killfeed shows AWP sniper headshot elimination",
        "Radar minimap top-left with bomb site A indicator",
        "Volumetric smoke cloud around Mirage mid"
    ]
    cs_match = match_game_from_evidence(cs_evidence)
    assert cs_match is not None
    assert cs_match["name"] == "Counter-Strike 2"
    assert cs_match["genre"] == "Tactical First-Person Shooter"


# =========================================================================
# 3. Multimodal Analysis & Grounding (Racing vs Shooter Grounding)
# =========================================================================

def test_multimodal_analysis_racing_game_grounding(synthetic_video):
    """
    Final Acceptance Criterion:
    A clip from a racing game must receive racing metadata, NOT generic FPS metadata.
    """
    mock_gemini_response = {
        "game": {
            "name": "Forza Horizon 5",
            "franchise": "Forza",
            "genre": "Sim-Cade Racing",
            "confidence": 0.95,
            "evidence": [
                "Circular digital tachometer and speedometer bottom-right",
                "Porsche 911 overtaking Ferrari on mountain hairpin",
                "Clean Racing and Drift skill notifications"
            ],
            "alternative_candidates": []
        },
        "gameplay": {
            "summary": "High-Speed Hairpin Drift Overtake",
            "events": [
                {
                    "name": "Precision Drift Overtake",
                    "start": 1.2,
                    "end": 3.8,
                    "confidence": 0.94,
                    "visual_evidence": "Vehicle slides through apex at 120 MPH overtaking lead car.",
                    "importance_score": 92.0
                }
            ]
        },
        "content": {
            "titles": [
                "One Insane Hairpin Drift Overtake in Forza Horizon 5!",
                "Unbelievable Precision: Clean Overtake at 120 MPH!",
                "Is This The Sickest Drift in Forza Horizon 5?!",
                "Threading The Needle on The Mountain Pass | Forza Horizon 5",
                "Watch This Insane Overtake! Pure Driving Skill"
            ],
            "description": "Watch this high-speed hairpin drift overtake in Forza Horizon 5. The driver carries 120 MPH through the apex to take the lead. NO FILTER. JUST GAMEPLAY.",
            "hashtags": ["#ForzaHorizon5", "#Forza", "#SimCadeRacing", "#Drift", "#GamingUncut", "#Shorts", "#Gameplay"],
            "captions": ["Clean drift entry!", "Unbelievable overtake!"],
            "pinned_comment": "Could you pull off this drift? Let us know below! 👇"
        },
        "analysis_status": "complete",
        "analysis_provider": "gemini"
    }

    with patch.object(gemini_provider, "_get_client", return_value=MagicMock()):
        with patch("backend.app.ai.gemini_provider._call_gemini_with_timeout", return_value=json.dumps(mock_gemini_response)):
            result = gemini_provider.analyze_multimodal_clip(synthetic_video, bypass_cache=True)

    # Verification: Must be racing, NEVER FPS
    assert result["game"]["name"] == "Forza Horizon 5"
    assert result["game"]["genre"] == "Sim-Cade Racing"
    assert len(result["content"]["titles"]) == 5
    for title in result["content"]["titles"]:
        assert "FPS" not in title
        assert "clutch action climax" not in title.lower()
        assert "Forza" in title or "Overtake" in title or "Drift" in title

    assert "#ForzaHorizon5" in result["content"]["hashtags"]
    assert "Forza Horizon 5" in result["content"]["description"]
    assert "hairpin drift" in result["content"]["description"].lower()


def test_multimodal_analysis_shooter_game_grounding(synthetic_video):
    """
    Final Acceptance Criterion:
    A clip from a shooter must receive shooter metadata, NOT racing metadata.
    """
    mock_gemini_response = {
        "game": {
            "name": "Counter-Strike 2",
            "franchise": "Counter-Strike",
            "genre": "Tactical First-Person Shooter",
            "confidence": 0.96,
            "evidence": [
                "AWP sniper rifle scope overlay",
                "Killfeed shows 3 consecutive sniper headshots",
                "Bomb planted on Site A timer ticking"
            ],
            "alternative_candidates": []
        },
        "gameplay": {
            "summary": "1v3 AWP Sniper Clutch on Mirage",
            "events": [
                {
                    "name": "1v3 AWP Sniper Clutch",
                    "start": 0.8,
                    "end": 4.5,
                    "confidence": 0.95,
                    "visual_evidence": "Player eliminates 3 defenders consecutively to secure defuse.",
                    "importance_score": 96.0
                }
            ]
        },
        "content": {
            "titles": [
                "One Insane 1v3 AWP Clutch in Counter-Strike 2!",
                "Unbelievable Sniper Reaction Time in CS2!",
                "The Coldest 1v3 AWP Defuse You'll See Today!",
                "Pure Precision: 3 Shots, 3 Headshots | CS2 Clutch",
                "How Did He Win This 1v3 Round in Counter-Strike 2?!"
            ],
            "description": "Watch this impossible 1v3 AWP sniper clutch in Counter-Strike 2 on Mirage. Down to 12 HP with the bomb ticking, he lands three consecutive headshots to secure the defuse. NO FILTER. JUST GAMEPLAY.",
            "hashtags": ["#CounterStrike2", "#CS2", "#AWP", "#Clutch", "#GamingUncut", "#Shorts", "#Gameplay"],
            "captions": ["3 enemy players remaining", "Triple headshot!"],
            "pinned_comment": "Rate this 1v3 clutch from 1 to 10 below! 👇"
        },
        "analysis_status": "complete",
        "analysis_provider": "gemini"
    }

    with patch.object(gemini_provider, "_get_client", return_value=MagicMock()):
        with patch("backend.app.ai.gemini_provider._call_gemini_with_timeout", return_value=json.dumps(mock_gemini_response)):
            result = gemini_provider.analyze_multimodal_clip(synthetic_video, bypass_cache=True)

    assert result["game"]["name"] == "Counter-Strike 2"
    assert result["game"]["genre"] == "Tactical First-Person Shooter"
    for title in result["content"]["titles"]:
        assert "drift" not in title.lower()
        assert "overtake" not in title.lower()
        assert "CS2" in title or "AWP" in title or "Counter-Strike" in title


# =========================================================================
# 4. Low Confidence & Unknown Game Handling
# =========================================================================

def test_multimodal_analysis_low_confidence_never_invents_game(synthetic_video):
    """
    Final Acceptance Criterion:
    A clip with unknown or low confidence identity must return 'Unknown Game'
    and neutral gameplay terminology; never a fabricated game name.
    """
    mock_low_conf_response = {
        "game": {
            "name": "Random Made Up Simulator",
            "confidence": 0.32,  # Low confidence!
            "evidence": ["Unclear footage, no recognizable HUD"],
            "alternative_candidates": []
        },
        "gameplay": {
            "summary": "Intense Combat Sequence",
            "events": [
                {
                    "name": "Combat Encounter",
                    "start": 1.0,
                    "end": 3.0,
                    "confidence": 0.70,
                    "visual_evidence": "Player character dodges enemy projectile",
                    "importance_score": 75.0
                }
            ]
        },
        "content": {
            "titles": [
                "Unbelievable Reaction Time in This Insane Gameplay Moment!",
                "Pure Intensity: Watch This Critical Play!",
                "One Crazy Clutch You Have to See to Believe!",
                "High-Energy Gameplay Highlights | Pure Skill",
                "Wait For The Final Play! Intense Gaming Moment"
            ],
            "description": "Watch this intense gameplay moment from Gaming Uncut. NO FILTER. JUST GAMEPLAY.",
            "hashtags": ["#GamingUncut", "#Shorts", "#Gameplay", "#Clutch"],
            "captions": ["Watch this play!"],
            "pinned_comment": "Rate this play!"
        },
        "analysis_status": "complete",
        "analysis_provider": "gemini"
    }

    with patch.object(gemini_provider, "_get_client", return_value=MagicMock()):
        with patch("backend.app.ai.gemini_provider._call_gemini_with_timeout", return_value=json.dumps(mock_low_conf_response)):
            result = gemini_provider.analyze_multimodal_clip(synthetic_video, bypass_cache=True)

    # Must be sanitized to Unknown Game with confidence 0.0
    assert result["game"]["name"] == "Unknown Game"
    assert result["game"]["confidence"] == 0.0
    for title in result["content"]["titles"]:
        assert "Random Made Up Simulator" not in title


# =========================================================================
# 5. Content Caching & Fingerprinting
# =========================================================================

def test_fingerprinting_and_caching(synthetic_video):
    """Verify video fingerprint is deterministic and caches repeat analysis."""
    fp1 = compute_video_fingerprint(synthetic_video)
    fp2 = compute_video_fingerprint(synthetic_video)
    assert fp1 == fp2, "Fingerprint must be deterministic"

    mock_analysis = {
        "game": {"name": "Black Myth: Wukong", "confidence": 0.95},
        "content": {"titles": ["Title 1", "Title 2", "Title 3", "Title 4", "Title 5"]},
        "analysis_status": "complete"
    }
    store_cached_analysis(fp1, mock_analysis)

    cached = get_cached_analysis(fp1)
    assert cached is not None
    assert cached["game"]["name"] == "Black Myth: Wukong"


# =========================================================================
# 6. Honest Heuristic Fallback When Offline
# =========================================================================

def test_heuristic_fallback_when_gemini_unconfigured(synthetic_video):
    """
    Requirement 7: If Gemini API is unavailable, mark analysis as HEURISTIC,
    never invent game identities, and provide honest event metadata.
    """
    with patch.object(gemini_provider, "_get_client", return_value=None):
        result = gemini_provider.analyze_multimodal_clip(
            video_path=synthetic_video,
            audio_peaks=[{"ts": 2.5, "db": -8.0}],
            bypass_cache=True
        )

    assert result["analysis_provider"] == "heuristic"
    assert result["analysis_status"] == "heuristic"
    assert result["game"]["name"] == "Unknown Game"
    assert result["game"]["confidence"] == 0.0
    assert len(result["content"]["titles"]) == 5
    assert "High-Intensity" in result["gameplay"]["summary"] or "Climax" in result["gameplay"]["summary"]


# =========================================================================
# 7. Database Persistence & Re-analysis Endpoint
# =========================================================================

def test_reanalyze_clip_endpoint(synthetic_video):
    """Verify POST /api/clips/{clip_id}/reanalyze updates clip metadata in DB with fresh multimodal analysis."""
    with SessionLocal() as db:
        src = SourceVideo(url="https://youtube.com/watch?v=inteltest01", title="Source Intel Test", duration=60.0, local_path=str(synthetic_video))
        db.add(src)
        db.commit()
        db.refresh(src)

        job = ProcessingJob(source_video_id=src.id, status="completed")
        db.add(job)
        db.commit()
        db.refresh(job)

        clip = GeneratedClip(job_id=job.id, output_path=str(synthetic_video), duration_sec=30.0)
        db.add(clip)
        db.commit()
        db.refresh(clip)

        meta = ClipMetadata(clip_id=clip.id, title="Old Generic Title", topic="Gaming")
        db.add(meta)
        db.commit()

        clip_id = clip.id

    mock_reanalysis = {
        "game": {
            "name": "Black Myth: Wukong",
            "franchise": "Black Myth",
            "genre": "Action RPG / Soulslike",
            "confidence": 0.94,
            "evidence": ["Staff of Wukong", "Tiger Vanguard boss health bar"],
            "alternative_candidates": []
        },
        "gameplay": {
            "summary": "Tiger Vanguard Boss Defeat",
            "events": [
                {
                    "name": "Tiger Vanguard Victory",
                    "start": 1.0,
                    "end": 4.5,
                    "confidence": 0.95,
                    "visual_evidence": "Final heavy staff strike eliminates boss.",
                    "importance_score": 95.0
                }
            ]
        },
        "content": {
            "titles": [
                "Insane Staff Combo Destroys Tiger Vanguard in Black Myth: Wukong!",
                "Flawless Boss Battle Finish in Black Myth: Wukong",
                "One Epic Victory Over Tiger Vanguard!",
                "Pure Martial Arts Combat | Black Myth: Wukong",
                "Watch The Final Strike! Black Myth: Wukong Boss Fight"
            ],
            "description": "Watch the Destined One execute a flawless heavy staff strike to defeat the Tiger Vanguard in Black Myth: Wukong. NO FILTER. JUST GAMEPLAY.",
            "hashtags": ["#BlackMythWukong", "#Wukong", "#Soulslike", "#GamingUncut", "#Shorts", "#Gameplay"],
            "captions": ["Heavy staff strike!", "Boss defeated!"],
            "pinned_comment": "How many tries did Tiger Vanguard take you? 👇"
        },
        "analysis_status": "complete",
        "analysis_provider": "gemini"
    }

    with patch.object(gemini_provider, "analyze_multimodal_clip", return_value=mock_reanalysis):
        res = client.post(f"/api/clips/{clip_id}/reanalyze")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "success"
        assert data["metadata"]["detected_game"] == "Black Myth: Wukong"
        assert len(data["metadata"]["candidate_titles"]) == 5

    # Verify DB persistence
    with SessionLocal() as db:
        updated_meta = db.query(ClipMetadata).filter_by(clip_id=clip_id).first()
        assert updated_meta is not None
        assert updated_meta.detected_game == "Black Myth: Wukong"
        assert updated_meta.game_confidence == 0.94
        assert updated_meta.analysis_provider == "gemini"
        assert "Tiger Vanguard" in updated_meta.title

    # Verify GET /clips/{clip_id}
    get_res = client.get(f"/api/clips/{clip_id}")
    assert get_res.status_code == 200
    clip_details = get_res.json()
    assert clip_details["detected_game"] == "Black Myth: Wukong"
    assert len(clip_details["candidate_titles"]) == 5
    assert clip_details["game_genre"] == "Action RPG / Soulslike"
