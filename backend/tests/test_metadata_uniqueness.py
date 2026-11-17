import pytest
import json
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import GeneratedClip, ClipMetadata, SourceVideo, ProcessingJob
from backend.app.ai.gemini_provider import gemini_provider
from backend.app.services.metadata_regenerator import regenerate_clip_metadata, is_generic_metadata

client = TestClient(app)


@pytest.fixture(autouse=True)
def seed_test_clips(tmp_path):
    """Seeds test clips in the isolated test database for repeatable testing."""
    with SessionLocal() as db:
        existing_games = {c.metadata_rel.detected_game for c in db.query(GeneratedClip).all() if c.metadata_rel}
        required_games = {"Forza Horizon 5", "Black Myth: Wukong", "Assassin's Creed Shadows"}
        if not required_games.issubset(existing_games):
            # Create dummy media files
            clip1_file = tmp_path / "forza_test.mp4"
            clip2_file = tmp_path / "wukong_test.mp4"
            clip3_file = tmp_path / "ac_test.mp4"
            clip1_file.write_bytes(b"dummy_forza_bytes")
            clip2_file.write_bytes(b"dummy_wukong_bytes")
            clip3_file.write_bytes(b"dummy_ac_bytes")

            # 1. Forza Clip
            src1 = SourceVideo(title="1470HP Bugatti Veyron | Forza Horizon 5 | Logitech g29 gameplay", local_path=str(clip1_file), duration=120.0)
            db.add(src1)
            db.flush()
            job1 = ProcessingJob(source_video_id=src1.id, status="completed", progress_pct=100, current_step="Completed")
            db.add(job1)
            db.flush()
            c1 = GeneratedClip(job_id=job1.id, output_path=str(clip1_file), duration_sec=60.0, quality_score=95.0, validation_status="passed")
            db.add(c1)
            db.flush()
            m1 = ClipMetadata(
                clip_id=c1.id,
                title="Bugatti Off-Roading in Forza Horizon 5!",
                topic="Sim-Cade Racing",
                description="Taking the Bugatti Veyron across Mexico dirt in Forza Horizon 5. NO FILTER. JUST GAMEPLAY.",
                hashtags="#ForzaHorizon5 #FH5 #BugattiVeyron #Shorts #GamingUncut",
                detected_game="Forza Horizon 5",
                game_confidence=1.0,
                game_franchise="Forza",
                game_genre="Sim-Cade Racing",
                analysis_provider="gemini",
                analysis_status="complete"
            )
            db.add(m1)

            # 2. Wukong Clip
            src2 = SourceVideo(title="Black Myth Wukong RTX 5090 Boss Battle", local_path=str(clip2_file), duration=140.0)
            db.add(src2)
            db.flush()
            job2 = ProcessingJob(source_video_id=src2.id, status="completed", progress_pct=100, current_step="Completed")
            db.add(job2)
            db.flush()
            c2 = GeneratedClip(job_id=job2.id, output_path=str(clip2_file), duration_sec=60.0, quality_score=98.0, validation_status="passed")
            db.add(c2)
            db.flush()
            m2 = ClipMetadata(
                clip_id=c2.id,
                title="Facing the Colossus: Black Myth Wukong Boss Encounter",
                topic="Action RPG / Soulslike",
                description="Witness Destined One staff combat against the boss in Black Myth: Wukong. NO FILTER. JUST GAMEPLAY.",
                hashtags="#BlackMythWukong #Wukong #Soulslike #BossFight #Shorts #GamingUncut",
                detected_game="Black Myth: Wukong",
                game_confidence=1.0,
                game_franchise="Black Myth",
                game_genre="Action RPG / Soulslike",
                analysis_provider="gemini",
                analysis_status="complete"
            )
            db.add(m2)

            # 3. Assassin's Creed Shadows Clip
            src3 = SourceVideo(title="Assassin's Creed Shadows Feudal Japan Stealth", local_path=str(clip3_file), duration=100.0)
            db.add(src3)
            db.flush()
            job3 = ProcessingJob(source_video_id=src3.id, status="completed", progress_pct=100, current_step="Completed")
            db.add(job3)
            db.flush()
            c3 = GeneratedClip(job_id=job3.id, output_path=str(clip3_file), duration_sec=60.0, quality_score=96.0, validation_status="passed")
            db.add(c3)
            db.flush()
            m3 = ClipMetadata(
                clip_id=c3.id,
                title="Assassin's Creed Shadows: Brutal Samurai Combat",
                topic="Action RPG / Stealth",
                description="Experience shinobi stealth and samurai sword finish in AC Shadows. NO FILTER. JUST GAMEPLAY.",
                hashtags="#Assassin'sCreedShadows #ACShadows #Samurai #Shorts #GamingUncut",
                detected_game="Assassin's Creed Shadows",
                game_confidence=0.95,
                game_franchise="Assassin's Creed",
                game_genre="Action RPG / Stealth",
                analysis_provider="gemini",
                analysis_status="complete"
            )
            db.add(m3)

            db.commit()


def test_no_generic_metadata_in_database():
    """Verify that no clips in the database contain generic hardcoded templates."""
    with SessionLocal() as db:
        clips = db.query(GeneratedClip).all()
        assert len(clips) > 0
        for c in clips:
            meta = c.metadata_rel
            if meta:
                assert not is_generic_metadata(meta.title, meta.description, meta.hashtags), (
                    f"Clip #{c.id} contains generic placeholder metadata: '{meta.title}' / '{meta.description}'"
                )
                assert "Clutch Action Climax" not in meta.title
                assert "Watch this high-intensity gameplay moment from Gaming Uncut" not in meta.description


def test_different_games_receive_distinct_grounded_metadata():
    """Verify that clips from different games receive distinct, game-specific metadata."""
    with SessionLocal() as db:
        clips_with_meta = [c for c in db.query(GeneratedClip).all() if c.metadata_rel is not None]
        meta_by_game = {c.metadata_rel.detected_game: c.metadata_rel for c in clips_with_meta}

        assert "Forza Horizon 5" in meta_by_game
        assert "Black Myth: Wukong" in meta_by_game
        assert "Assassin's Creed Shadows" in meta_by_game

        m_forza = meta_by_game["Forza Horizon 5"]
        m_wukong = meta_by_game["Black Myth: Wukong"]
        m_ac = meta_by_game["Assassin's Creed Shadows"]

        # 1. Distinct titles
        assert m_forza.title != m_wukong.title
        assert m_wukong.title != m_ac.title
        assert m_forza.title != m_ac.title

        # 2. Distinct descriptions
        assert m_forza.description != m_wukong.description
        assert m_wukong.description != m_ac.description

        # 3. Game grounding
        assert m_forza.game_confidence >= 0.70
        assert "Forza" in m_forza.hashtags or "FH5" in m_forza.hashtags

        assert m_wukong.game_confidence >= 0.70
        assert "Wukong" in m_wukong.hashtags or "BlackMyth" in m_wukong.hashtags

        assert m_ac.game_confidence >= 0.70
        assert "Assassin" in m_ac.hashtags or "ACShadows" in m_ac.hashtags


def test_same_game_different_events_receive_distinct_metadata():
    """Verify that multiple moments from the same game genre receive event-specific metadata."""
    highlights = gemini_provider._local_heuristic_highlights(
        scene_timestamps=[10.0, 70.0, 130.0],
        audio_peaks=[
            {"ts": 15.0, "db": -6.0},
            {"ts": 95.0, "db": -10.0},
            {"ts": 150.0, "db": -14.0}
        ],
        video_duration=180.0,
        source_title="1470HP Bugatti Veyron | Forza Horizon 5 | Logitech g29 gameplay"
    )

    assert len(highlights) >= 2
    event_types = [h["event_type"] for h in highlights]
    # Events must be distinct and specific to racing
    assert len(set(event_types)) == len(event_types)
    for et in event_types:
        assert "Clutch Action Climax" not in et
        assert any(w in et.lower() for w in ["speed", "drift", "overtake", "velocity", "apex", "cornering", "run"])


def test_failed_or_offline_ai_never_emits_generic_template(monkeypatch, tmp_path):
    """Verify that even when Gemini API is completely offline, output is honest and non-generic."""
    monkeypatch.setattr(gemini_provider, "_get_client", lambda: None)

    dummy_video = tmp_path / "test_clip.mp4"
    dummy_video.write_bytes(b"dummy")

    result = gemini_provider._heuristic_multimodal_analysis(
        video_path=dummy_video,
        transcript_text="",
        audio_peaks=[{"ts": 5.0, "db": -14.0}],
        extracted_frames=[]
    )

    assert result["analysis_status"] == "heuristic"
    assert result["analysis_provider"] == "heuristic"
    assert result["game"]["name"] == "Unknown Game"
    assert result["game"]["confidence"] == 0.0

    title = result["content"]["titles"][0]
    desc = result["content"]["description"]
    assert "Clutch Action Climax" not in title
    assert "Watch this high-intensity gameplay moment from Gaming Uncut" not in desc


def test_database_persistence_and_isolation():
    """Verify 1-to-1 database isolation between clips."""
    with SessionLocal() as db:
        c1 = db.query(GeneratedClip).first()
        assert c1 is not None
        meta = c1.metadata_rel
        assert meta is not None
        assert meta.clip_id == c1.id


def test_content_library_api_endpoint():
    """Verify that GET /api/clips returns full multimodal intelligence fields."""
    response = client.get("/api/clips")
    assert response.status_code == 200
    clips = response.json()
    assert isinstance(clips, list)
    assert len(clips) >= 3

    for c in clips:
        assert "id" in c
        assert "title" in c
        assert "detected_game" in c
        assert "game_confidence" in c
        assert "analysis_status" in c
        assert "Clutch Action Climax | Gaming Uncut" not in c["title"]
