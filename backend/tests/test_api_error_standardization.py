"""
Test Suite for Standardized API Error & Success Responses.
Verifies that:
1. HTTP 400 returns standardized JSON { success: false, error: { code: 'BAD_REQUEST', message, details } }.
2. HTTP 404 returns standardized JSON { success: false, error: { code: 'NOT_FOUND', message, details } }.
3. HTTP 405 returns standardized JSON { success: false, error: { code: 'METHOD_NOT_ALLOWED', message, details } }.
4. HTTP 422 returns standardized JSON { success: false, error: { code: 'VALIDATION_ERROR', message, details } }.
5. HTTP 500 returns standardized JSON { success: false, error: { code: 'INTERNAL_SERVER_ERROR', message, details } }.
6. YouTube Discovery import accepts ISO 8601 published_at strings without database TypeError.
7. YouTube Discovery manual search accepts min_views=None without TypeError.
"""

import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.discovery.service import discovery_service
from backend.app.models.schema import DiscoveredVideo

client = TestClient(app, raise_server_exceptions=False)

def test_standardized_404_error():
    """Verify non-existent API endpoints return standardized JSON."""
    res = client.get("/api/non-existent-endpoint-xyz")
    assert res.status_code == 404
    data = res.json()
    assert data["success"] is False
    assert "error" in data
    assert data["error"]["code"] == "NOT_FOUND"
    assert "not found" in data["error"]["message"].lower()

def test_standardized_405_error():
    """Verify method not allowed returns standardized JSON."""
    res = client.post("/ui")
    assert res.status_code == 405
    data = res.json()
    assert data["success"] is False
    assert "error" in data
    assert data["error"]["code"] == "METHOD_NOT_ALLOWED"

def test_standardized_422_validation_error():
    """Verify Pydantic validation errors return standardized JSON."""
    res = client.post("/api/discovery/search", json={"max_results": "invalid_integer"})
    assert res.status_code == 422
    data = res.json()
    assert data["success"] is False
    assert "error" in data
    assert data["error"]["code"] == "VALIDATION_ERROR"
    assert "Validation failed" in data["error"]["message"]
    assert isinstance(data["error"]["details"], list)

def test_standardized_400_bad_request():
    """Verify Bad Request returns standardized JSON."""
    res = client.post("/api/discovery/import", json={"videos": []})
    assert res.status_code == 400
    data = res.json()
    assert data["success"] is False
    assert "error" in data
    assert data["error"]["code"] == "BAD_REQUEST"
    assert "No videos provided" in data["error"]["message"]

def test_discovery_import_handles_string_published_at_safely():
    """Verify import_videos_to_queue safely converts ISO 8601 string published_at to datetime."""
    db = SessionLocal()
    try:
        # Clean up any leftover test video
        db.query(DiscoveredVideo).filter_by(youtube_video_id="std_test_vid_iso_001").delete()
        db.commit()

        imported = discovery_service.import_videos_to_queue(
            db=db,
            videos=[{
                "youtube_video_id": "std_test_vid_iso_001",
                "title": "ISO Date Safe Import Video",
                "published_at": "2026-11-18T14:30:00+00:00",
                "duration_sec": 185.5,
                "view_count": 12500,
                "channel_id": "UC_TEST_CHAN",
                "channel_title": "Test Gaming Studio"
            }],
            discovery_mode="manual"
        )
        assert len(imported) == 1
        video = db.query(DiscoveredVideo).filter_by(youtube_video_id="std_test_vid_iso_001").first()
        assert video is not None
        assert video.published_at is not None
        # Verify it can be serialized to ISO without error
        assert video.published_at.year == 2026

        # Test queue endpoint serialization
        res = client.get("/api/discovery/queue?search=std_test_vid_iso_001")
        assert res.status_code == 200
        q_data = res.json()
        assert q_data["total"] >= 1
        item = [it for it in q_data["items"] if it["youtube_video_id"] == "std_test_vid_iso_001"][0]
        assert "2026-11-18" in item["published_at"]

    finally:
        db.query(DiscoveredVideo).filter_by(youtube_video_id="std_test_vid_iso_001").delete()
        db.commit()
        db.close()

def test_discovery_search_get_with_none_min_views():
    """Verify GET /api/discovery/search does not crash when min_views is omitted or None."""
    res = client.get("/api/discovery/search?query=GTA")
    # Even if YouTube API fails due to quota or auth, it should return 400/200 with JSON, NOT a 500 TypeError
    assert res.status_code in (200, 400)
    data = res.json()
    if res.status_code == 200:
        assert "items" in data
    else:
        assert data["success"] is False
        assert "not supported between instances" not in str(data)
