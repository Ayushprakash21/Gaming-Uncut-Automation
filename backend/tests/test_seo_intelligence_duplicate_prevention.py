"""
Comprehensive Regression Test Suite for:
GAMING UNCUT — AI SEO INTELLIGENCE + UNIQUE METADATA + DUPLICATE PUBLISHING PREVENTION

Covers all 15 required verification targets:
1. Two distinct clips receive different titles.
2. Same clip retains consistent metadata across platforms.
3. Near-duplicate titles trigger regeneration.
4. Titles accurately reflect detected gameplay events.
5. Unknown game does not generate fabricated game-specific keywords.
6. YouTube title length never exceeds 100 characters.
7. Descriptions are not keyword-stuffed.
8. Platform-specific captions are generated.
9. Duplicate queue insertion is rejected.
10. Concurrent workers cannot publish the same clip twice.
11. Network timeout reconciliation does not duplicate posts.
12. Explicit republish remains possible.
13. SEO quality score and metadata confidence are persisted.
14. Manual metadata edits are preserved.
15. Existing published records remain unchanged.
"""

import pytest
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    GeneratedClip, ClipMetadata, ClipSeoMetadata, SourceVideo,
    ProcessingJob, PublishingJob, PlatformUpload, PublishingIdempotencyRecord
)
from backend.app.ai.seo_metadata_engine import (
    seo_metadata_engine, compute_title_similarity, normalize_title_for_comparison,
    FORBIDDEN_GENERIC_PATTERNS
)
from backend.app.publishers.idempotency import (
    IdempotencyManager, build_idempotency_key, PublishingState
)

client = TestClient(app)


@pytest.fixture
def test_db_clips(tmp_path):
    """Creates isolated test clip records for SEO engine testing."""
    with SessionLocal() as db:
        # Create dummy mp4 files
        clip1_path = tmp_path / "gta_chase.mp4"
        clip2_path = tmp_path / "valorant_clutch.mp4"
        clip3_path = tmp_path / "unknown_action.mp4"
        clip1_path.write_bytes(b"dummy_gta_bytes")
        clip2_path.write_bytes(b"dummy_val_bytes")
        clip3_path.write_bytes(b"dummy_unknown_bytes")

        # Source 1: GTA 6
        src1 = SourceVideo(title="GTA 6 Vice City Police Scooter Chase Gameplay", local_path=str(clip1_path), duration=120.0)
        db.add(src1)
        db.flush()
        pj1 = ProcessingJob(source_video_id=src1.id, status="completed", progress_pct=100)
        db.add(pj1)
        db.flush()
        c1 = GeneratedClip(job_id=pj1.id, output_path=str(clip1_path), duration_sec=59.0, width=1080, height=1920)
        db.add(c1)
        db.flush()

        # Source 2: Valorant
        src2 = SourceVideo(title="Valorant 1v4 Clutch Site Retake Round", local_path=str(clip2_path), duration=90.0)
        db.add(src2)
        db.flush()
        pj2 = ProcessingJob(source_video_id=src2.id, status="completed", progress_pct=100)
        db.add(pj2)
        db.flush()
        c2 = GeneratedClip(job_id=pj2.id, output_path=str(clip2_path), duration_sec=45.0, width=1080, height=1920)
        db.add(c2)
        db.flush()

        # Source 3: Unknown game
        src3 = SourceVideo(title="Raw Gameplay Clip Capture Sequence 2026", local_path=str(clip3_path), duration=60.0)
        db.add(src3)
        db.flush()
        pj3 = ProcessingJob(source_video_id=src3.id, status="completed", progress_pct=100)
        db.add(pj3)
        db.flush()
        c3 = GeneratedClip(job_id=pj3.id, output_path=str(clip3_path), duration_sec=50.0, width=1080, height=1920)
        db.add(c3)
        db.flush()

        db.commit()
        return {"gta_clip_id": c1.id, "val_clip_id": c2.id, "unknown_clip_id": c3.id}


# =============================================================================
# Test 1: Two distinct clips receive different titles
# =============================================================================
def test_two_distinct_clips_receive_different_titles(test_db_clips):
    """Ensures that distinct clips are never assigned identical or template fallback titles."""
    with SessionLocal() as db:
        meta1 = seo_metadata_engine.generate_and_persist(test_db_clips["gta_clip_id"], db)
        meta2 = seo_metadata_engine.generate_and_persist(test_db_clips["val_clip_id"], db)

        title1 = meta1["youtube"]["title"]
        title2 = meta2["youtube"]["title"]

        assert title1 != title2
        sim = compute_title_similarity(title1, title2)
        assert sim < 0.50, f"Titles are too similar ({sim:.2f}): '{title1}' vs '{title2}'"
        assert "Pure Precision" not in title1
        assert "Pure Precision" not in title2


# =============================================================================
# Test 2: Same clip retains consistent metadata across platforms
# =============================================================================
def test_same_clip_retains_consistent_metadata_across_platforms(test_db_clips):
    """Verifies that YouTube, Instagram, and Facebook packages for the same clip share consistent game and event identity."""
    with SessionLocal() as db:
        pkg = seo_metadata_engine.generate_and_persist(test_db_clips["gta_clip_id"], db)

        yt_title = pkg["youtube"]["title"]
        ig_caption = pkg["instagram"]["caption"]
        fb_title = pkg["facebook"]["title"]
        game = pkg["game"]

        assert game in yt_title
        assert game in ig_caption
        assert game in fb_title
        assert "GTA" in game or "Grand Theft Auto" in game


# =============================================================================
# Test 3: Near-duplicate titles trigger regeneration
# =============================================================================
def test_near_duplicate_titles_trigger_regeneration(test_db_clips, tmp_path):
    """Verifies that if a new clip produces a title near-duplicate to an existing clip, the engine resolves it."""
    with SessionLocal() as db:
        # Generate metadata for clip 1
        pkg1 = seo_metadata_engine.generate_and_persist(test_db_clips["gta_clip_id"], db)
        title1 = pkg1["youtube"]["title"]

        # Create a second similar GTA clip
        clip_path = tmp_path / "gta_scooter_2.mp4"
        clip_path.write_bytes(b"dummy_gta_2")
        src = SourceVideo(title="GTA 6 Vice City Police Scooter Chase Gameplay Part 2", local_path=str(clip_path), duration=120.0)
        db.add(src)
        db.flush()
        pj = ProcessingJob(source_video_id=src.id, status="completed", progress_pct=100)
        db.add(pj)
        db.flush()
        c4 = GeneratedClip(job_id=pj.id, output_path=str(clip_path), duration_sec=55.0)
        db.add(c4)
        db.commit()

        # Generate metadata for clip 4
        pkg4 = seo_metadata_engine.generate_and_persist(c4.id, db)
        title4 = pkg4["youtube"]["title"]

        # Titles must be distinct and differentiated
        assert title1 != title4
        dup_info = pkg4.get("duplicate_detection", {})
        # Should record resolution
        assert dup_info["status"] in ("unique", "regenerated_unique")


# =============================================================================
# Test 4: Titles accurately reflect detected gameplay events
# =============================================================================
def test_titles_accurately_reflect_detected_gameplay_events(test_db_clips):
    """Ensures detected events (chase, clutch) are naturally placed in generated titles."""
    with SessionLocal() as db:
        gta_pkg = seo_metadata_engine.generate_and_persist(test_db_clips["gta_clip_id"], db)
        val_pkg = seo_metadata_engine.generate_and_persist(test_db_clips["val_clip_id"], db)

        gta_title = gta_pkg["youtube"]["title"].lower()
        val_title = val_pkg["youtube"]["title"].lower()

        assert any(k in gta_title for k in ["chase", "scooter", "escape", "pursuit"])
        assert any(k in val_title for k in ["clutch", "retake", "round", "site"])


# =============================================================================
# Test 5: Unknown game does not generate fabricated game-specific keywords
# =============================================================================
def test_unknown_game_does_not_generate_fabricated_game_specific_keywords(test_db_clips):
    """Verifies that when a game is unknown, the engine does not hallucinate fake games, characters, or missions."""
    with SessionLocal() as db:
        unk_pkg = seo_metadata_engine.generate_and_persist(test_db_clips["unknown_clip_id"], db)

        game = unk_pkg["game"]
        title = unk_pkg["youtube"]["title"]
        tags = unk_pkg["youtube"]["tags"]

        assert game == "Unknown Game"
        # Must not fabricate popular titles
        assert "GTA" not in title
        assert "Valorant" not in title
        assert "Call of Duty" not in title
        assert "Red Dead" not in title
        # Tags must be generic gaming tags
        assert all(t not in ["gta 6", "valorant", "arthur morgan", "vice city"] for t in tags)


# =============================================================================
# Test 6: YouTube title length never exceeds 100 characters
# =============================================================================
def test_youtube_title_length_never_exceeds_100_characters(test_db_clips, tmp_path):
    """Strictly enforces YouTube Shorts 100-character ceiling across all candidate generations."""
    with SessionLocal() as db:
        # Create clip with an exceptionally long source title
        long_title = "Grand Theft Auto V Online Extreme High Speed Highway Police Pursuit Evasion With Supercar Near Misses"
        c_path = tmp_path / "long_clip.mp4"
        c_path.write_bytes(b"long")
        src = SourceVideo(title=long_title, local_path=str(c_path), duration=60.0)
        db.add(src)
        db.flush()
        pj = ProcessingJob(source_video_id=src.id, status="completed")
        db.add(pj)
        db.flush()
        c = GeneratedClip(job_id=pj.id, output_path=str(c_path))
        db.add(c)
        db.commit()

        pkg = seo_metadata_engine.generate_and_persist(c.id, db)
        yt_title = pkg["youtube"]["title"]

        assert len(yt_title) <= 100
        assert len(pkg["facebook"]["title"]) <= 100


# =============================================================================
# Test 7: Descriptions are not keyword-stuffed
# =============================================================================
def test_descriptions_are_not_keyword_stuffed(test_db_clips):
    """Verifies that descriptions are readable, value-dense paragraphs with subtle brand attribution, not 50 repetitive tags."""
    with SessionLocal() as db:
        pkg = seo_metadata_engine.generate_and_persist(test_db_clips["gta_clip_id"], db)
        desc = pkg["youtube"]["description"]

        assert "NO FILTER. JUST GAMEPLAY." in desc
        # Ensure hashtag count in description is reasonable (<= 10)
        hashtags = [w for w in desc.split() if w.startswith("#")]
        assert len(hashtags) <= 8


# =============================================================================
# Test 8: Platform-specific captions are generated
# =============================================================================
def test_platform_specific_captions_are_generated(test_db_clips):
    """Verifies that YouTube, Instagram, and Facebook each receive tailored packages (e.g. Instagram alt-text, Facebook title)."""
    with SessionLocal() as db:
        pkg = seo_metadata_engine.generate_and_persist(test_db_clips["gta_clip_id"], db)

        # Instagram must have alt_text
        ig = pkg["instagram"]
        assert "alt_text" in ig and len(ig["alt_text"]) > 10
        assert "Rate this sequence" in ig["caption"] or "👇" in ig["caption"]

        # Facebook must have distinct title & caption
        fb = pkg["facebook"]
        assert "title" in fb and len(fb["title"]) > 10
        assert "description" in fb and len(fb["description"]) > 10


# =============================================================================
# Test 9: Duplicate queue insertion is rejected
# =============================================================================
def test_duplicate_queue_insertion_is_rejected(test_db_clips):
    """Verifies that attempting to queue a clip that already has a queue entry is rejected with HTTP 409 unless allow_republish=True."""
    clip_id = test_db_clips["gta_clip_id"]

    # 1. First queue insertion succeeds
    res1 = client.post(f"/api/clips/{clip_id}/queue", json={"platforms": ["youtube"], "allow_republish": False})
    assert res1.status_code == 200

    # 2. Duplicate queue insertion is rejected
    res2 = client.post(f"/api/clips/{clip_id}/queue", json={"platforms": ["youtube"], "allow_republish": False})
    assert res2.status_code == 409
    assert "Duplicate queue insertion rejected" in res2.json()["detail"]


# =============================================================================
# Test 10: Concurrent workers cannot publish the same clip twice
# =============================================================================
def test_concurrent_workers_cannot_publish_same_clip_twice(test_db_clips):
    """Verifies database-level atomic lease acquisition prevents two workers from claiming the same job."""
    with SessionLocal() as db:
        clip_id = test_db_clips["val_clip_id"]
        # Create a PublishingJob
        pj = PublishingJob(clip_id=clip_id, scheduled_time=datetime.utcnow(), status="pending", approval_status="approved")
        db.add(pj)
        db.commit()

        # Worker 1 acquires lease
        success1, reason1, job1 = IdempotencyManager.claim_job_atomically(
            db, pj.id, worker_id="worker_alpha_1", lease_seconds=300
        )
        assert success1 is True
        assert job1.worker_id == "worker_alpha_1"

        # Worker 2 attempts concurrent claim
        success2, reason2, job2 = IdempotencyManager.claim_job_atomically(
            db, pj.id, worker_id="worker_beta_2", lease_seconds=300
        )
        assert success2 is False
        assert "actively held by worker 'worker_alpha_1'" in reason2


# =============================================================================
# Test 11: Network timeout reconciliation does not duplicate posts
# =============================================================================
def test_network_timeout_reconciliation_does_not_duplicate_posts(test_db_clips):
    """Verifies that when a clip already has a published idempotency record, reconciliation prevents duplicate re-upload."""
    with SessionLocal() as db:
        clip_id = test_db_clips["gta_clip_id"]
        idemp_key = build_idempotency_key(clip_id, "instagram", "test_acc_1", "reels")

        # Record exists as PUBLISHED
        rec = PublishingIdempotencyRecord(
            idempotency_key=idemp_key,
            clip_id=clip_id,
            platform="instagram",
            account_id="test_acc_1",
            destination="reels",
            status="PUBLISHED",
            external_post_id="remote_ig_media_999",
            external_url="https://instagram.com/reel/remote_ig_media_999"
        )
        db.add(rec)
        db.commit()

        # Check reconciliation lookup
        reconciled = IdempotencyManager.check_existing_publication(
            db, "instagram", clip_id, "test_acc_1", "reels"
        )
        assert reconciled is not None
        assert reconciled["status"] == "published"
        assert reconciled["post_id"] == "remote_ig_media_999"


# =============================================================================
# Test 12: Explicit republish remains possible
# =============================================================================
def test_explicit_republish_remains_possible(test_db_clips):
    """Verifies that passing allow_republish=True allows explicit user re-queuing and publishing override."""
    clip_id = test_db_clips["gta_clip_id"]

    # Re-queue with explicit allow_republish=True succeeds even though job exists
    res = client.post(f"/api/clips/{clip_id}/queue", json={"platforms": ["youtube"], "allow_republish": True})
    assert res.status_code == 200
    assert "successfully added to Publishing Queue" in res.json()["message"]


# =============================================================================
# Test 13: SEO quality score and metadata confidence are persisted
# =============================================================================
def test_seo_quality_score_and_metadata_confidence_are_persisted(test_db_clips):
    """Verifies that 0-100 SEO quality score and confidence are saved to ClipMetadata."""
    with SessionLocal() as db:
        pkg = seo_metadata_engine.generate_and_persist(test_db_clips["gta_clip_id"], db)

        meta = db.query(ClipMetadata).filter_by(clip_id=test_db_clips["gta_clip_id"]).first()
        assert meta is not None
        assert meta.seo_score > 0.0
        assert meta.metadata_confidence > 0.0
        assert meta.primary_keyword is not None
        assert meta.metadata_version >= 1


# =============================================================================
# Test 14: Manual metadata edits are preserved
# =============================================================================
def test_manual_metadata_edits_are_preserved(test_db_clips):
    """Verifies that user manual edits set is_manually_edited=True and are preserved against automated overwrites."""
    clip_id = test_db_clips["val_clip_id"]

    # 1. Update metadata manually via API
    custom_title = "Valorant 1v4 Clutch Changes the Entire Round | Custom Edit"
    res = client.put(f"/api/clips/{clip_id}/metadata", json={
        "title": custom_title,
        "youtube_title": custom_title,
        "youtube_desc": "Custom user description.",
        "primary_keyword": "Valorant clutch round"
    })
    assert res.status_code == 200

    # 2. Verify is_manually_edited is True in database
    with SessionLocal() as db:
        meta = db.query(ClipMetadata).filter_by(clip_id=clip_id).first()
        assert meta.is_manually_edited is True
        assert meta.title == custom_title[:95]

        # 3. Running automated generate_and_persist without force_regenerate does NOT overwrite
        pkg = seo_metadata_engine.generate_and_persist(clip_id, db, force_regenerate=False)
        assert pkg["is_manually_edited"] is True
        assert custom_title[:90] in pkg["youtube"]["title"]


# =============================================================================
# Test 15: Existing published records remain unchanged
# =============================================================================
def test_existing_published_records_remain_unchanged(test_db_clips):
    """Verifies historical audit report runs safely without mutating or deleting published database entries."""
    with SessionLocal() as db:
        # Mark an existing job as published
        job = PublishingJob(
            clip_id=test_db_clips["unknown_clip_id"],
            scheduled_time=datetime.utcnow() - timedelta(days=2),
            status="published"
        )
        db.add(job)
        db.flush()
        up = PlatformUpload(
            publishing_job_id=job.id,
            platform="youtube",
            status="published",
            external_post_id="historical_yt_post_777"
        )
        db.add(up)
        db.commit()

        initial_count = db.query(PlatformUpload).filter_by(status="published").count()

        # Run historical audit
        audit = seo_metadata_engine.generate_historical_audit_report(db)
        assert audit["status"] == "audit_complete"

        # Verify published records count is identical
        final_count = db.query(PlatformUpload).filter_by(status="published").count()
        assert final_count == initial_count
        # Verify the historical post was untouched
        check_up = db.query(PlatformUpload).filter_by(id=up.id).first()
        assert check_up.external_post_id == "historical_yt_post_777"
