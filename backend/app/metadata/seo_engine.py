"""
Platform-Specific SEO & Growth Intelligence Engine for Gaming Uncut.
Produces grounded, distinct optimization packages for:
1. YouTube Shorts (5 distinct titles, search intent, tags, thumbnail hook)
2. Instagram Reels (3 caption variations, max 5 hashtags, on-screen text, share CTA)
3. Facebook Reels (3 description variations, community hook, engagement CTA)
Integrates local-first AI routing, keyword research, and transparent quality scoring.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from backend.app.database.session import SessionLocal
from backend.app.models.schema import GeneratedClip, ClipMetadata, ClipSeoMetadata
from backend.app.ai.llm_router import llm_router
from backend.app.metadata.keyword_research import keyword_research_service
from backend.app.metadata.seo_scorer import seo_scorer
from backend.app.ai.game_knowledge import lookup_game_by_alias

logger = logging.getLogger("gaming_uncut.seo_engine")

class SEOEngine:
    def generate_clip_seo(
        self,
        clip_id: int,
        db: Optional[Session] = None,
        target_platforms: Optional[List[str]] = None,
        bypass_cache: bool = False,
        custom_keywords: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Generates platform-tailored SEO metadata packages for an existing GeneratedClip.
        """
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True
        try:
            clip = db.query(GeneratedClip).filter_by(id=clip_id).first()
            if not clip:
                raise ValueError(f"Generated clip #{clip_id} not found.")

            meta = clip.metadata_rel
            platforms_to_run = target_platforms or platforms or ["youtube", "instagram", "facebook"]

            # 1. Harvest intelligence context
            game_name = (meta.detected_game if meta else "Unknown Game") or "Unknown Game"
            user_game = getattr(meta, "user_game_name", None)
            game_confidence = float(meta.game_confidence or 0.0) if meta else 0.0
            game_mismatch = bool(getattr(meta, "game_mismatch_flag", False))
            event_type = meta.gameplay_summary if meta and meta.gameplay_summary else "Action Gameplay"
            game_genre = meta.game_genre if meta and meta.game_genre else "Action"
            transcript = ""

            # Check existing events
            events_data = []
            if meta and meta.detected_events_json:
                try:
                    events_data = json.loads(meta.detected_events_json)
                except Exception:
                    pass

            if events_data and isinstance(events_data, list) and len(events_data) > 0:
                first_ev = events_data[0]
                if isinstance(first_ev, dict) and first_ev.get("name"):
                    event_type = first_ev["name"]

            # 2. Gather keyword research
            kw_data = keyword_research_service.research_keywords(
                game_name=game_name,
                event_type=event_type,
                custom_keywords=custom_keywords,
                bypass_cache=bypass_cache,
                db=db
            )

            results = {}

            # 3. Generate platform-specific packages
            if "youtube" in platforms_to_run:
                results["youtube"] = self._generate_youtube_seo(
                    clip=clip,
                    meta=meta,
                    game_name=game_name,
                    user_game=user_game,
                    game_confidence=game_confidence,
                    game_mismatch=game_mismatch,
                    event_type=event_type,
                    kw_data=kw_data,
                    db=db
                )

            if "instagram" in platforms_to_run:
                results["instagram"] = self._generate_instagram_seo(
                    clip=clip,
                    meta=meta,
                    game_name=game_name,
                    user_game=user_game,
                    game_confidence=game_confidence,
                    game_mismatch=game_mismatch,
                    event_type=event_type,
                    kw_data=kw_data,
                    db=db
                )

            if "facebook" in platforms_to_run:
                results["facebook"] = self._generate_facebook_seo(
                    clip=clip,
                    meta=meta,
                    game_name=game_name,
                    user_game=user_game,
                    game_confidence=game_confidence,
                    game_mismatch=game_mismatch,
                    event_type=event_type,
                    kw_data=kw_data,
                    db=db
                )

            db.commit()
            return {
                "clip_id": clip_id,
                "game_name": game_name,
                "user_game_name": user_game,
                "game_mismatch_flag": game_mismatch,
                "platforms": results
            }
        finally:
            if close_db:
                db.close()

    # =========================================================================
    # YouTube Shorts SEO Generator
    # =========================================================================
    def _generate_youtube_seo(
        self,
        clip: GeneratedClip,
        meta: Optional[ClipMetadata],
        game_name: str,
        user_game: Optional[str],
        game_confidence: float,
        game_mismatch: bool,
        event_type: str,
        kw_data: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Produces 5 distinct titles, search intent, thumbnail text, tags, and description for Shorts."""
        primary_kw = kw_data.get("primary_keyword", f"{game_name} gameplay")
        secondary_kws = kw_data.get("secondary_keywords", [])
        tags = kw_data.get("recommended_tags", [])
        hashtags = kw_data.get("recommended_hashtags", [])

        # Fallback heuristic generator
        def heuristic_yt():
            game_suffix = f" in {game_name}" if game_name != "Unknown Game" else ""
            t1 = f"Pure Skill{game_suffix}: {event_type}! #Shorts"
            t2 = f"One Insane Play{game_suffix}! #Shorts"
            t3 = f"Unbelievable Reaction Speed{game_suffix}! #Shorts"
            t4 = f"Wait For The Climax! {event_type} #Shorts"
            t5 = f"How Did This Happen{game_suffix}?! #Shorts"
            desc = (
                f"Raw unedited gameplay capture featuring {event_type.lower()}{game_suffix}. "
                f"No filter, just pure skill and synchronized gameplay audio.\n\n"
                f"Subscribe to Gaming Uncut for daily raw gaming highlights!\n\n"
                f"{' '.join(hashtags[:5])}"
            )
            return {
                "recommended_title": t1[:95],
                "candidate_titles": [t1[:95], t2[:95], t3[:95], t4[:95], t5[:95]],
                "description": desc,
                "primary_keyword": primary_kw,
                "secondary_keywords": secondary_kws[:5],
                "tags": tags[:12],
                "hashtags": hashtags[:5],
                "search_phrases": [f"{game_name} clutch", f"{game_name} highlights", f"{game_name} 60fps"],
                "gameplay_event_keywords": [event_type.lower(), "clutch play", "gaming reaction"],
                "thumbnail_hook_text": "UNREAL PLAY!",
                "opening_hook": f"Watch this {event_type.lower()} sequence!",
                "search_intent": "entertainment",
                "cta": "Rate this gameplay from 1 to 10 in the comments below! 👇"
            }

        prompt = f"""
You are the YouTube SEO Director for 'Gaming Uncut' (NO FILTER. JUST GAMEPLAY).
Generate a YouTube Shorts SEO package for a 60s gameplay clip.
Context:
- Game: {game_name} (Confidence: {int(game_confidence*100)}%)
- Gameplay Event: {event_type}
- Primary Search Keyword: "{primary_kw}"
- Secondary Keywords: {secondary_kws}

RULES:
- Generate 5 distinct, factual, click-worthy titles (35-85 chars) ending with '#Shorts'.
- Do NOT invent fictional characters or missions.
- Keep description under 400 characters, value-dense, with CTA.
- Classify search intent as one of: 'entertainment', 'clutch_skill', 'how_to_play', 'cinematic_run'.

Output ONLY valid JSON matching this schema:
{{
  "recommended_title": "string",
  "candidate_titles": ["title 1", "title 2", "title 3", "title 4", "title 5"],
  "description": "string",
  "primary_keyword": "string",
  "secondary_keywords": ["k1", "k2", "k3"],
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
  "hashtags": ["#Shorts", "#tag2", "#tag3"],
  "search_phrases": ["phrase 1", "phrase 2"],
  "gameplay_event_keywords": ["event1", "event2"],
  "thumbnail_hook_text": "3-5 WORDS MAX",
  "opening_hook": "Spoken or text hook for first 3 seconds",
  "search_intent": "entertainment",
  "cta": "engagement prompt"
}}
"""
        res = llm_router.generate(
            task_type="titles",
            prompt=prompt,
            json_mode=True,
            temperature=0.25,
            heuristic_fallback_fn=heuristic_yt
        )
        data = res.get("json") or heuristic_yt()

        # Score the YouTube package
        score_res = seo_scorer.score_youtube_seo(
            title=data.get("recommended_title", ""),
            description=data.get("description", ""),
            primary_keyword=data.get("primary_keyword", primary_kw),
            secondary_keywords=data.get("secondary_keywords", []),
            tags=data.get("tags", []),
            hashtags=data.get("hashtags", []),
            game_name=game_name,
            game_confidence=game_confidence,
            game_mismatch=game_mismatch,
            thumbnail_text=data.get("thumbnail_hook_text", ""),
            opening_hook=data.get("opening_hook", ""),
            search_intent=data.get("search_intent", "")
        )

        # Persist or update in database
        db_rec = self._save_or_update_seo_record(
            clip_id=clip.id,
            platform="youtube",
            data=data,
            score_res=score_res,
            provider=res.get("provider", "heuristic"),
            model=res.get("model", "heuristic"),
            db=db
        )

        return {
            **data,
            "seo_score": score_res["overall_score"],
            "score_breakdown": score_res["breakdown"],
            "warnings": score_res["warnings"],
            "missing_opportunities": score_res["missing_opportunities"],
            "recommendations": score_res["recommendations"],
            "ai_provider": res.get("provider", "heuristic"),
            "approval_status": db_rec.approval_status
        }

    # =========================================================================
    # Instagram Reels SEO Generator
    # =========================================================================
    def _generate_instagram_seo(
        self,
        clip: GeneratedClip,
        meta: Optional[ClipMetadata],
        game_name: str,
        user_game: Optional[str],
        game_confidence: float,
        game_mismatch: bool,
        event_type: str,
        kw_data: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Produces 3 caption variations, max 5 hashtags, on-screen text, and save/share CTAs."""
        primary_kw = kw_data.get("primary_keyword", f"{game_name} reel")
        game_tag = f"#{re.sub(r'[^a-zA-Z0-9]', '', game_name)}" if game_name != "Unknown Game" else "#Gaming"
        default_hashtags = [game_tag, "#GamingReels", "#Gamer", "#Gameplay", "#GamingUncut"][:5]

        def heuristic_ig():
            c1 = f"Wait for the reaction speed on this one! 🔥\n\nPure skill in {game_name}.\n\nSave this for your next session! 👇\n\n{' '.join(default_hashtags)}"
            c2 = f"How did he pull this off?! 😱\n\nHigh-intensity {event_type.lower()} in {game_name}. No filter, just gameplay.\n\nTag a duo who needs to see this! 👇\n\n{' '.join(default_hashtags)}"
            c3 = f"1 in a million moment in {game_name} ⚡\n\nWatch till the end for the climax.\n\nFollow @GamingUncut for daily raw clips!\n\n{' '.join(default_hashtags)}"
            return {
                "recommended_caption": c1,
                "caption_variations": [c1, c2, c3],
                "primary_keyword": primary_kw,
                "hashtags": default_hashtags,
                "opening_hook": "Wait for the reaction speed! 🔥",
                "onscreen_text": "WAIT FOR THE CLIMAX ⚡",
                "cta": "Save this reel and share with your squad! 👇",
                "topic_suggestions": [game_name, "Gaming Highlights", "Clutch Plays"]
            }

        prompt = f"""
You are the Instagram Growth Strategist for 'Gaming Uncut' (Tagline: NO FILTER. JUST GAMEPLAY).
Generate an Instagram Reels SEO & discovery package for a 60s vertical gaming reel.
Context:
- Game: {game_name}
- Action: {event_type}
- Keyword: "{primary_kw}"

REQUIREMENTS:
- Generate 3 distinct caption variations (conversational, hook in first 60 chars before fold, clear save/share CTA).
- Exactly 3 to 5 hyper-targeted hashtags (NO hashtag stuffing; Instagram algorithm penalizes >5 tags).
- On-screen text recommendation (short, high visual contrast).
- Actionable CTA for saves and shares.

Output ONLY valid JSON matching this schema:
{{
  "recommended_caption": "Full recommended caption string with hook, body, CTA, and 3-5 tags",
  "caption_variations": ["caption 1", "caption 2", "caption 3"],
  "primary_keyword": "string",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"],
  "opening_hook": "First 2-3 seconds hook",
  "onscreen_text": "Overlay text for video",
  "cta": "Save and share call to action",
  "topic_suggestions": ["topic1", "topic2"]
}}
"""
        res = llm_router.generate(
            task_type="captions",
            prompt=prompt,
            json_mode=True,
            temperature=0.3,
            heuristic_fallback_fn=heuristic_ig
        )
        data = res.get("json") or heuristic_ig()

        # Enforce Section 3B rule: Max 5 recommended hashtags
        clean_tags = [h.strip() for h in data.get("hashtags", []) if h.strip().startswith("#")]
        data["hashtags"] = clean_tags[:5] if clean_tags else default_hashtags
        data["call_to_action"] = data.get("call_to_action") or data.get("cta") or "Save and share for more gameplay!"
        data["onscreen_text"] = data.get("onscreen_text") or "NO FILTER. JUST GAMEPLAY."

        score_res = seo_scorer.score_instagram_seo(
            caption=data.get("recommended_caption", ""),
            primary_keyword=data.get("primary_keyword", primary_kw),
            hashtags=data.get("hashtags", []),
            game_name=game_name,
            game_mismatch=game_mismatch,
            cta=data.get("cta", ""),
            onscreen_text=data.get("onscreen_text", "")
        )

        db_rec = self._save_or_update_seo_record(
            clip_id=clip.id,
            platform="instagram",
            data=data,
            score_res=score_res,
            provider=res.get("provider", "heuristic"),
            model=res.get("model", "heuristic"),
            db=db
        )

        return {
            **data,
            "seo_score": score_res["overall_score"],
            "score_breakdown": score_res["breakdown"],
            "warnings": score_res["warnings"],
            "missing_opportunities": score_res["missing_opportunities"],
            "recommendations": score_res["recommendations"],
            "ai_provider": res.get("provider", "heuristic"),
            "approval_status": db_rec.approval_status
        }

    # =========================================================================
    # Facebook Reels SEO Generator
    # =========================================================================
    def _generate_facebook_seo(
        self,
        clip: GeneratedClip,
        meta: Optional[ClipMetadata],
        game_name: str,
        user_game: Optional[str],
        game_confidence: float,
        game_mismatch: bool,
        event_type: str,
        kw_data: Dict[str, Any],
        db: Session
    ) -> Dict[str, Any]:
        """Produces 3 description variations, community discussion hooks, and shareability recommendations."""
        primary_kw = kw_data.get("primary_keyword", f"{game_name} highlights")
        fb_tags = [f"#{re.sub(r'[^a-zA-Z0-9]', '', game_name)}", "#Gaming", "#Reels"][:3]

        def heuristic_fb():
            d1 = f"Pure gameplay focus in {game_name}. Watch this insane {event_type.lower()} moment! What would you have done here? Drop your thoughts below. 👇\n\n{' '.join(fb_tags)}"
            d2 = f"Unbelievable reaction timing in this {game_name} sequence. Pure precision from start to finish! Tag a gamer who plays like this. 👇\n\n{' '.join(fb_tags)}"
            d3 = f"Is this the cleanest play of the week? Raw {game_name} gameplay captured without commentary. React with your rating! 🎮\n\n{' '.join(fb_tags)}"
            return {
                "recommended_description": d1,
                "description_variations": [d1, d2, d3],
                "primary_keyword": primary_kw,
                "hashtags": fb_tags,
                "opening_hook": f"Pure gameplay focus in {game_name}!",
                "cta": "What would you have done in this situation? Comment below! 👇",
                "topic_suggestions": [game_name, "Gaming Community", "Video Games"]
            }

        prompt = f"""
You are the Facebook Reels Community Director for 'Gaming Uncut'.
Generate a Facebook Reels SEO and audience package for a 60s gameplay reel.
Context:
- Game: {game_name}
- Event: {event_type}
- Keyword: "{primary_kw}"

REQUIREMENTS:
- Generate 3 distinct description variations (broad audience appeal, community question, shareable hook).
- 2 to 4 relevant hashtags.
- Discussion-prompting CTA.

Output ONLY valid JSON matching this schema:
{{
  "recommended_description": "Full description text with hook, explanation, CTA, and 2-3 hashtags",
  "description_variations": ["desc 1", "desc 2", "desc 3"],
  "primary_keyword": "string",
  "hashtags": ["#tag1", "#tag2", "#tag3"],
  "opening_hook": "Hook sentence",
  "cta": "Community discussion CTA",
  "topic_suggestions": ["topic1", "topic2"]
}}
"""
        res = llm_router.generate(
            task_type="descriptions",
            prompt=prompt,
            json_mode=True,
            temperature=0.3,
            heuristic_fallback_fn=heuristic_fb
        )
        data = res.get("json") or heuristic_fb()
        data["community_discussion_cta"] = data.get("community_discussion_cta") or data.get("cta") or "What rating would you give this play? Drop it in the comments!"
        data["call_to_action"] = data["community_discussion_cta"]

        score_res = seo_scorer.score_facebook_seo(
            description=data.get("recommended_description", ""),
            primary_keyword=data.get("primary_keyword", primary_kw),
            hashtags=data.get("hashtags", []),
            game_name=game_name,
            game_mismatch=game_mismatch,
            cta=data.get("cta", "")
        )

        db_rec = self._save_or_update_seo_record(
            clip_id=clip.id,
            platform="facebook",
            data=data,
            score_res=score_res,
            provider=res.get("provider", "heuristic"),
            model=res.get("model", "heuristic"),
            db=db
        )

        return {
            **data,
            "seo_score": score_res["overall_score"],
            "score_breakdown": score_res["breakdown"],
            "warnings": score_res["warnings"],
            "missing_opportunities": score_res["missing_opportunities"],
            "recommendations": score_res["recommendations"],
            "ai_provider": res.get("provider", "heuristic"),
            "approval_status": db_rec.approval_status
        }

    def _save_or_update_seo_record(
        self,
        clip_id: int,
        platform: str,
        data: Dict[str, Any],
        score_res: Dict[str, Any],
        provider: str,
        model: str,
        db: Session
    ) -> ClipSeoMetadata:
        """Persists or updates ClipSeoMetadata 1-to-1 per clip and platform."""
        rec = db.query(ClipSeoMetadata).filter_by(clip_id=clip_id, platform=platform).first()
        if not rec:
            rec = ClipSeoMetadata(
                clip_id=clip_id,
                platform=platform,
                version=1,
                approval_status="pending"
            )
            db.add(rec)
        else:
            rec.version += 1

        rec.recommended_title = data.get("recommended_title") or data.get("recommended_caption") or data.get("recommended_description")
        rec.candidate_titles_json = json.dumps(data.get("candidate_titles") or data.get("caption_variations") or data.get("description_variations") or [])
        rec.recommended_caption = data.get("recommended_caption")
        rec.description = data.get("description") or data.get("recommended_description") or data.get("recommended_caption")
        rec.primary_keyword = data.get("primary_keyword")
        rec.secondary_keywords_json = json.dumps(data.get("secondary_keywords") or [])
        rec.tags_json = json.dumps(data.get("tags") or [])
        rec.hashtags_json = json.dumps(data.get("hashtags") or [])
        rec.search_phrases_json = json.dumps(data.get("search_phrases") or [])
        rec.gameplay_event_keywords_json = json.dumps(data.get("gameplay_event_keywords") or [])
        rec.thumbnail_hook_text = data.get("thumbnail_hook_text")
        rec.opening_hook = data.get("opening_hook")
        rec.search_intent = data.get("search_intent")
        rec.cta = data.get("cta")
        rec.onscreen_text = data.get("onscreen_text")
        rec.topic_suggestions_json = json.dumps(data.get("topic_suggestions") or [])
        rec.seo_score = score_res["overall_score"]
        rec.score_breakdown_json = json.dumps(score_res["breakdown"])
        rec.warnings_json = json.dumps(score_res["warnings"])
        rec.recommendations_json = json.dumps(score_res["recommendations"])
        rec.ai_provider = provider
        rec.ai_model = model
        rec.updated_at = datetime.utcnow()

        return rec

seo_engine = SEOEngine()
