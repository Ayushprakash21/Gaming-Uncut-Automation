"""
Keyword Research & Search Intelligence Service for Gaming Uncut Automation.
Pluggable data sources: YouTube Data API v3, YouTube Search Suggestions,
Historical Database Performance, and Grounded AI Semantic Clustering.
Strictly distinguishes measured API metrics from AI-generated estimates.
"""

import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import requests
from sqlalchemy.orm import Session

from backend.app.database.session import SessionLocal
from backend.app.models.schema import KeywordResearchRecord, PublishingAnalytics, PlatformUpload, GeneratedClip, ClipMetadata
from backend.app.ai.game_knowledge import lookup_game_by_alias, GAME_REGISTRY
from backend.app.ai.llm_router import llm_router

logger = logging.getLogger("gaming_uncut.keyword_research")

class KeywordResearchService:
    def __init__(self):
        self._memory_cache: Dict[str, Dict[str, Any]] = {}

    def get_youtube_suggestions(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """
        Retrieves real-time user search queries from YouTube autocomplete service.
        Strictly labeled as measured platform suggestions.
        """
        clean_q = query.strip()
        if not clean_q:
            return []

        url = f"https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q={urllib.parse.quote(clean_q)}"
        try:
            resp = requests.get(url, timeout=3.0, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                data = resp.json()
                if len(data) > 1 and isinstance(data[1], list):
                    now = datetime.utcnow()
                    return [
                        {
                            "keyword": phrase,
                            "source": "youtube_autocomplete",
                            "is_measured": True,
                            "trend_direction": "stable",
                            "retrieved_at": now.isoformat()
                        }
                        for phrase in data[1][:limit]
                    ]
        except Exception as e:
            logger.debug(f"YouTube suggestion lookup exception: {e}")

        return []

    def get_historical_top_keywords(self, game_name: str, db: Session, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Extracts proven keywords from Gaming Uncut's own highest-performing historical clips.
        """
        try:
            # Query top uploads by views that match game_name
            records = (
                db.query(PublishingAnalytics, ClipMetadata)
                .join(PlatformUpload, PublishingAnalytics.platform_upload_id == PlatformUpload.id)
                .join(GeneratedClip, PlatformUpload.publishing_job_id == GeneratedClip.id) # approximate match
                .join(ClipMetadata, GeneratedClip.id == ClipMetadata.clip_id)
                .filter(ClipMetadata.detected_game.ilike(f"%{game_name}%"))
                .order_by(PublishingAnalytics.views.desc())
                .limit(10)
                .all()
            )
            extracted = set()
            results = []
            now = datetime.utcnow().isoformat()
            for pa, meta in records:
                if meta.hashtags:
                    tags = [t.replace("#", "").strip() for t in meta.hashtags.split() if len(t) > 2]
                    for t in tags[:2]:
                        if t.lower() not in extracted:
                            extracted.add(t.lower())
                            results.append({
                                "keyword": t,
                                "source": "historical_db",
                                "is_measured": True,
                                "performance_views": pa.views,
                                "retrieved_at": now
                            })
                if len(results) >= limit:
                    break
            return results
        except Exception as e:
            logger.debug(f"Historical keyword extraction query exception: {e}")
            return []

    def research_keywords(
        self,
        game_name: str,
        event_type: str = "",
        custom_keywords: Optional[List[str]] = None,
        bypass_cache: bool = False,
        db: Optional[Session] = None
    ) -> Dict[str, Any]:
        """
        Aggregates keyword research from all available data sources with caching.
        Returns primary keyword, secondary keywords, search phrases, and verified tags.
        """
        cache_key = f"{game_name}_{event_type}".lower().strip()
        if not bypass_cache and cache_key in self._memory_cache:
            entry = self._memory_cache[cache_key]
            if (datetime.utcnow() - entry["timestamp"]).total_seconds() < 86400:
                return entry["data"]

        # Check DB persistent cache
        own_db = False
        if db is None:
            db = SessionLocal()
            own_db = True

        try:
            if not bypass_cache:
                cached_db = db.query(KeywordResearchRecord).filter_by(game_name=game_name).all()
                if cached_db and len(cached_db) >= 3:
                    fresh_records = [
                        r for r in cached_db
                        if r.expires_at and r.expires_at > datetime.utcnow()
                    ]
                    if fresh_records:
                        data = self._format_research_payload(game_name, event_type, fresh_records)
                        self._memory_cache[cache_key] = {"timestamp": datetime.utcnow(), "data": data}
                        return data

            measured_results: List[Dict[str, Any]] = []

            # 1. Live measured search queries via YouTube Autocomplete
            if game_name and game_name != "Unknown Game":
                measured_results.extend(self.get_youtube_suggestions(f"{game_name} gameplay", limit=5))
                if event_type:
                    clean_event = event_type.split("&")[0].strip()
                    measured_results.extend(self.get_youtube_suggestions(f"{game_name} {clean_event}", limit=3))

            # 2. Historical performance data
            if db:
                historical = self.get_historical_top_keywords(game_name, db, limit=4)
                measured_results.extend(historical)

            # 3. Incorporate user-provided custom keywords
            if custom_keywords:
                now_str = datetime.utcnow().isoformat()
                for ck in custom_keywords:
                    clean_k = ck.strip()
                    if clean_k:
                        measured_results.append({
                            "keyword": clean_k,
                            "source": "user_provided",
                            "is_measured": True,
                            "retrieved_at": now_str
                        })

            # 4. Grounded AI semantic grouping & long-tail expansion
            game_spec = lookup_game_by_alias(game_name) if game_name else None
            ai_keywords = self._generate_ai_semantic_keywords(game_name, event_type, game_spec)

            # Combine and deduplicate
            all_records = []
            seen_keywords = set()

            for item in measured_results:
                k_norm = item["keyword"].lower().strip()
                if k_norm and k_norm not in seen_keywords:
                    seen_keywords.add(k_norm)
                    all_records.append(item)

            for item in ai_keywords:
                k_norm = item["keyword"].lower().strip()
                if k_norm and k_norm not in seen_keywords:
                    seen_keywords.add(k_norm)
                    all_records.append(item)

            # Persist to database cache
            expires_at = datetime.utcnow() + timedelta(days=2)
            for rec in all_records:
                try:
                    db_rec = KeywordResearchRecord(
                        keyword=rec["keyword"][:200],
                        game_name=game_name[:150] if game_name else None,
                        platform="all",
                        source=rec.get("source", "ai_estimate"),
                        is_measured=bool(rec.get("is_measured", False)),
                        trend_direction=rec.get("trend_direction", "stable"),
                        retrieved_at=datetime.utcnow(),
                        expires_at=expires_at
                    )
                    db.add(db_rec)
                except Exception:
                    pass
            db.commit()

            data = self._format_research_payload(game_name, event_type, all_records)
            self._memory_cache[cache_key] = {"timestamp": datetime.utcnow(), "data": data}
            return data

        finally:
            if own_db:
                db.close()

    def _generate_ai_semantic_keywords(
        self,
        game_name: str,
        event_type: str,
        game_spec: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Uses Local LLM or deterministic heuristics to group semantic gaming phrases.
        Explicitly marked with is_measured=False and source="ai_estimate".
        """
        now_str = datetime.utcnow().isoformat()
        is_unknown = not game_name or game_name == "Unknown Game"

        def fallback_heuristic() -> List[Dict[str, Any]]:
            items = []
            if not is_unknown:
                clean_game = re.sub(r'[^a-zA-Z0-9 ]', '', game_name)
                items.append(f"{clean_game} gameplay")
                items.append(f"{clean_game} shorts")
                items.append(f"{clean_game} best moments")
                if event_type:
                    items.append(f"{clean_game} {event_type.lower()}")
            else:
                items.extend(["gaming clutch", "insane gaming reaction", "clutch gameplay moments", "gaming shorts"])

            return [
                {
                    "keyword": k,
                    "source": "ai_estimate",
                    "is_measured": False,
                    "trend_direction": "stable",
                    "retrieved_at": now_str
                }
                for k in items
            ]

        prompt = f"""
Given the gameplay context below, generate 6 high-intent search phrases gamers type when searching for this gameplay.
Game: {game_name if not is_unknown else 'Competitive Action Gameplay'}
Action: {event_type or 'High skill clutch play'}
Output ONLY a JSON array of strings:
["phrase 1", "phrase 2", "phrase 3", "phrase 4", "phrase 5", "phrase 6"]
"""
        res = llm_router.generate(
            task_type="keyword_grouping",
            prompt=prompt,
            json_mode=True,
            temperature=0.2,
            heuristic_fallback_fn=fallback_heuristic
        )

        phrases = res.get("json")
        if isinstance(phrases, list) and phrases:
            return [
                {
                    "keyword": str(p).strip(),
                    "source": "ai_estimate",
                    "is_measured": False,
                    "trend_direction": "stable",
                    "retrieved_at": now_str
                }
                for p in phrases if str(p).strip()
            ]

        return fallback_heuristic()

    def _format_research_payload(
        self,
        game_name: str,
        event_type: str,
        records: List[Any]
    ) -> Dict[str, Any]:
        """Formats the list of keyword records into structured SEO targets."""
        parsed_records = []
        for r in records:
            if isinstance(r, dict):
                parsed_records.append(r)
            else:
                parsed_records.append({
                    "keyword": getattr(r, "keyword", ""),
                    "source": getattr(r, "source", "ai_estimate"),
                    "is_measured": getattr(r, "is_measured", False),
                    "trend_direction": getattr(r, "trend_direction", "stable"),
                    "retrieved_at": getattr(r, "retrieved_at", datetime.utcnow()).isoformat() if hasattr(getattr(r, "retrieved_at", None), "isoformat") else str(getattr(r, "retrieved_at", ""))
                })

        # Separate primary, secondary, and phrases
        measured_items = [p for p in parsed_records if p.get("is_measured")]
        ai_items = [p for p in parsed_records if not p.get("is_measured")]

        primary = f"{game_name} gameplay" if (game_name and game_name != "Unknown Game") else "Gaming Clutch"
        if measured_items:
            primary = measured_items[0]["keyword"]

        secondary = [p["keyword"] for p in (measured_items[1:6] + ai_items[:4])]
        tags = [p["keyword"] for p in parsed_records[:15]]
        hashtags = [
            f"#{re.sub(r'[^a-zA-Z0-9]', '', p['keyword'])}"
            for p in parsed_records[:10]
            if len(p["keyword"]) > 2
        ]

        return {
            "game_name": game_name,
            "primary_keyword": primary,
            "secondary_keywords": secondary[:6],
            "recommended_tags": tags[:12],
            "recommended_hashtags": hashtags[:8],
            "raw_records": parsed_records,
            "measured_count": len(measured_items),
            "ai_estimate_count": len(ai_items),
            "retrieved_at": datetime.utcnow().isoformat()
        }

keyword_research_service = KeywordResearchService()
