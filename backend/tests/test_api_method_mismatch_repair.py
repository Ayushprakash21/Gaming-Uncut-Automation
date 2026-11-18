"""
Regression tests for API Method Mismatch Repair (Issue A: YouTube Search, Issue B: Compute Insights).
Verifies:
1. /api/discovery/search supports POST (JSON body) and GET (query params) with and without trailing slash.
2. /api/seo/growth/compute-insights supports POST and GET with and without trailing slash.
3. FastAPI SPA catch-all correctly raises 404 (NOT 405 Method Not Allowed) for non-existent API routes.
4. Input validation and error contracts.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.app.main import app

@pytest.fixture
def client():
    return TestClient(app)


# ============================================================
# 1. Gameplay Discovery Search Dual Method & Trailing Slash Tests
# ============================================================

def test_discovery_search_post_success(client):
    """POST /api/discovery/search accepts JSON payload and returns search results."""
    mock_res = {
        "items": [{"id": "vid1", "title": "GTA V Epic Stunt"}],
        "total_results": 1,
        "next_page_token": None,
        "search_id": 10
    }
    with patch("backend.app.discovery.service.discovery_service.search_manual", return_value=mock_res):
        res = client.post("/api/discovery/search", json={"query": "GTA V", "max_results": 10})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["total_results"] == 1
        assert len(data["items"]) == 1


def test_discovery_search_post_trailing_slash(client):
    """POST /api/discovery/search/ with trailing slash succeeds without 405."""
    mock_res = {"items": [], "total_results": 0, "next_page_token": None, "search_id": 11}
    with patch("backend.app.discovery.service.discovery_service.search_manual", return_value=mock_res):
        res = client.post("/api/discovery/search/", json={"query": "Valorant"})
        assert res.status_code == 200, res.text
        assert res.json()["search_id"] == 11


def test_discovery_search_get_success(client):
    """GET /api/discovery/search accepts query parameters and returns search results."""
    mock_res = {
        "items": [{"id": "vid2", "title": "Cyberpunk 2077 Best Mods"}],
        "total_results": 1,
        "next_page_token": None,
        "search_id": 12
    }
    with patch("backend.app.discovery.service.discovery_service.search_manual", return_value=mock_res):
        res = client.get("/api/discovery/search?query=Cyberpunk&duration=medium&max_results=5")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["total_results"] == 1
        assert data["items"][0]["id"] == "vid2"


def test_discovery_search_get_trailing_slash(client):
    """GET /api/discovery/search/ with trailing slash succeeds without 405."""
    mock_res = {"items": [], "total_results": 0, "next_page_token": None, "search_id": 13}
    with patch("backend.app.discovery.service.discovery_service.search_manual", return_value=mock_res):
        res = client.get("/api/discovery/search/?query=RDR2")
        assert res.status_code == 200, res.text


# ============================================================
# 2. SEO Growth Insights Dual Method & Trailing Slash Tests
# ============================================================

def test_compute_insights_post_success(client):
    """POST /api/seo/growth/compute-insights computes and returns insights."""
    mock_insights = [{"insight_type": "CTR_IMPACT", "recommendation": "Use punchy hooks"}]
    with patch("backend.app.analytics.growth_intelligence.growth_intelligence_service.compute_continuous_learning_insights", return_value=mock_insights):
        res = client.post("/api/seo/growth/compute-insights")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "completed"
        assert data["insights_generated"] == 1


def test_compute_insights_post_trailing_slash(client):
    """POST /api/seo/growth/compute-insights/ succeeds without 405."""
    with patch("backend.app.analytics.growth_intelligence.growth_intelligence_service.compute_continuous_learning_insights", return_value=[]):
        res = client.post("/api/seo/growth/compute-insights/")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "completed"


def test_compute_insights_get_success(client):
    """GET /api/seo/growth/compute-insights succeeds without 405."""
    with patch("backend.app.analytics.growth_intelligence.growth_intelligence_service.compute_continuous_learning_insights", return_value=[]):
        res = client.get("/api/seo/growth/compute-insights")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "completed"


def test_compute_insights_get_trailing_slash(client):
    """GET /api/seo/growth/compute-insights/ succeeds without 405."""
    with patch("backend.app.analytics.growth_intelligence.growth_intelligence_service.compute_continuous_learning_insights", return_value=[]):
        res = client.get("/api/seo/growth/compute-insights/")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "completed"


# ============================================================
# 3. SPA Catch-All Interception Protection Tests
# ============================================================

def test_unmatched_api_post_returns_404_not_405(client):
    """POST to a non-existent /api route must return 404 Not Found, never 405 Method Not Allowed."""
    res = client.post("/api/nonexistent/endpoint")
    assert res.status_code == 404
    assert "not found" in res.json().get("detail", "").lower()


def test_unmatched_api_get_returns_404_not_spa_html(client):
    """GET to a non-existent /api route must return JSON 404, never index.html."""
    res = client.get("/api/nonexistent/endpoint")
    assert res.status_code == 404
    assert "not found" in res.json().get("detail", "").lower()
