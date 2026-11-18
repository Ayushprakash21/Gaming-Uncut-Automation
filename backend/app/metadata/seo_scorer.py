"""
Transparent 0-100 SEO Quality Scorer for Gaming Uncut Automation.
Evaluates 7 distinct dimensions:
1. Keyword Relevance (25 pts)
2. Gameplay Accuracy (20 pts)
3. Title/Caption Quality (15 pts)
4. Description Relevance (15 pts)
5. Hashtag Relevance (10 pts)
6. Audience & Search Intent (10 pts)
7. Metadata Completeness (5 pts)
Surfaces diagnostic warnings, missing optimization opportunities, and actionable advice.
"""

import re
from typing import Dict, Any, List, Optional

class SEOScorer:
    """
    Computes deterministic, transparent internal SEO quality scores (0-100).
    Explicitly labeled as an internal quality index, not a platform view guarantee.
    """

    def score_youtube_seo(
        self,
        title: str,
        description: str,
        primary_keyword: str,
        secondary_keywords: List[str],
        tags: List[str],
        hashtags: List[str],
        game_name: str,
        game_confidence: float,
        game_mismatch: bool,
        thumbnail_text: str = "",
        opening_hook: str = "",
        search_intent: str = ""
    ) -> Dict[str, Any]:
        """Calculates YouTube Shorts SEO quality score."""
        breakdown = {
            "keyword_relevance": 0.0,
            "gameplay_accuracy": 0.0,
            "title_quality": 0.0,
            "description_relevance": 0.0,
            "hashtag_relevance": 0.0,
            "audience_search_intent": 0.0,
            "metadata_completeness": 0.0,
        }
        warnings: List[str] = []
        recommendations: List[str] = []
        missing_opportunities: List[str] = []

        # 1. Keyword Relevance (25 pts max)
        title_lower = (title or "").lower()
        desc_lower = (description or "").lower()
        pk_lower = (primary_keyword or "").lower().strip()

        if pk_lower:
            if pk_lower in title_lower:
                breakdown["keyword_relevance"] += 12.0
            else:
                missing_opportunities.append(f"Include primary keyword '{primary_keyword}' in YouTube title.")

            if pk_lower in desc_lower:
                breakdown["keyword_relevance"] += 8.0
            else:
                missing_opportunities.append(f"Primary keyword '{primary_keyword}' is missing from the description.")

        # Secondary keywords match
        matched_secondary = [sk for sk in secondary_keywords if sk.lower() in desc_lower or sk.lower() in title_lower]
        if matched_secondary:
            breakdown["keyword_relevance"] += min(5.0, len(matched_secondary) * 2.5)

        # 2. Gameplay Accuracy (20 pts max)
        if game_mismatch:
            warnings.append("Conflict detected between user-supplied game name and visual footage.")
            breakdown["gameplay_accuracy"] += 2.0
        elif game_name and game_name != "Unknown Game":
            conf_pts = min(12.0, game_confidence * 14.0)
            breakdown["gameplay_accuracy"] += conf_pts
            if game_name.lower() in title_lower or game_name.lower() in desc_lower:
                breakdown["gameplay_accuracy"] += 8.0
            else:
                missing_opportunities.append(f"Game name '{game_name}' is not mentioned in metadata.")
        else:
            # Unknown Game: neutral terminology
            breakdown["gameplay_accuracy"] += 10.0
            recommendations.append("Specify game name in ingestion input to unlock game-specific SEO keywords.")

        # 3. Title Quality (15 pts max)
        t_len = len(title or "")
        if 35 <= t_len <= 90:
            breakdown["title_quality"] += 10.0
        elif t_len > 90:
            breakdown["title_quality"] += 6.0
            warnings.append(f"Title is {t_len} characters. Over 90 characters may get cut off on mobile YouTube Shorts shelf.")
        elif t_len > 0:
            breakdown["title_quality"] += 5.0
            recommendations.append("Expand title with action keyword for better click-through rate.")

        if "#shorts" in title_lower:
            breakdown["title_quality"] += 5.0
        else:
            recommendations.append("Add '#Shorts' at the end of YouTube title to aid Shorts shelf classification.")

        # 4. Description Relevance (15 pts max)
        d_len = len(description or "")
        if d_len >= 120:
            breakdown["description_relevance"] += 15.0
        elif d_len >= 60:
            breakdown["description_relevance"] += 10.0
            recommendations.append("Add 1-2 more sentences explaining the gameplay moment to improve search discovery.")
        else:
            breakdown["description_relevance"] += 4.0
            warnings.append("Description is under 60 characters; minimal contextual value for search algorithms.")

        # 5. Hashtag Relevance (10 pts max)
        clean_tags = [h for h in hashtags if h.startswith("#")]
        if 2 <= len(clean_tags) <= 6:
            breakdown["hashtag_relevance"] += 10.0
        elif len(clean_tags) > 6:
            breakdown["hashtag_relevance"] += 6.0
            warnings.append(f"{len(clean_tags)} hashtags found. YouTube recommends 3-5 focused hashtags.")
        elif clean_tags:
            breakdown["hashtag_relevance"] += 5.0
        else:
            missing_opportunities.append("No hashtags provided. Add 3-5 tags including #Shorts.")

        # 6. Audience & Search Intent (10 pts max)
        if search_intent and search_intent != "general":
            breakdown["audience_search_intent"] += 5.0
        if any(term in desc_lower for term in ["comment", "rate", "subscribe", "watch", "like"]):
            breakdown["audience_search_intent"] += 5.0
        else:
            recommendations.append("Include an engagement prompt (e.g. 'Rate this clutch from 1 to 10').")

        # 7. Metadata Completeness (5 pts max)
        if thumbnail_text:
            breakdown["metadata_completeness"] += 2.5
        if opening_hook:
            breakdown["metadata_completeness"] += 2.5

        total = sum(breakdown.values())
        return {
            "platform": "youtube",
            "overall_score": round(min(100.0, total), 1),
            "breakdown": {k: round(v, 1) for k, v in breakdown.items()},
            "warnings": warnings,
            "missing_opportunities": missing_opportunities,
            "recommendations": recommendations,
            "scoring_engine_version": "2.0.0"
        }

    def score_instagram_seo(
        self,
        caption: str,
        primary_keyword: str,
        hashtags: List[str],
        game_name: str,
        game_mismatch: bool,
        cta: str = "",
        onscreen_text: str = ""
    ) -> Dict[str, Any]:
        """Calculates Instagram Reels SEO quality score."""
        breakdown = {
            "keyword_relevance": 0.0,
            "gameplay_accuracy": 0.0,
            "title_quality": 0.0,  # Hook & first 80 chars
            "description_relevance": 0.0,
            "hashtag_relevance": 0.0,
            "audience_search_intent": 0.0,
            "metadata_completeness": 0.0,
        }
        warnings: List[str] = []
        recommendations: List[str] = []
        missing_opportunities: List[str] = []

        c_lower = (caption or "").lower()
        pk_lower = (primary_keyword or "").lower()

        # 1. Keyword Relevance (25 pts)
        if pk_lower and pk_lower in c_lower:
            breakdown["keyword_relevance"] += 20.0
        else:
            missing_opportunities.append(f"Incorporate search keyword '{primary_keyword}' into caption prose.")

        if game_name and game_name.lower() in c_lower:
            breakdown["keyword_relevance"] += 5.0

        # 2. Gameplay Accuracy (20 pts)
        if game_mismatch:
            warnings.append("Conflict detected with user-supplied game name.")
            breakdown["gameplay_accuracy"] += 5.0
        else:
            breakdown["gameplay_accuracy"] += 20.0

        # 3. First 80 Chars / Hook Quality (15 pts)
        first_line = (caption or "").split("\n")[0].strip()
        if len(first_line) >= 20 and len(first_line) <= 100:
            breakdown["title_quality"] += 15.0
        else:
            breakdown["title_quality"] += 8.0
            recommendations.append("Craft a concise 30-70 character opening hook before the '...more' fold.")

        # 4. Description Depth (15 pts)
        if len(caption or "") >= 100:
            breakdown["description_relevance"] += 15.0
        else:
            breakdown["description_relevance"] += 8.0
            recommendations.append("Instagram SEO indexes caption text. Add 2 descriptive sentences about the sequence.")

        # 5. Hashtag Discipline (10 pts) - Section 3B: Max 5 recommended hashtags
        clean_tags = [h for h in hashtags if h.startswith("#")]
        if 3 <= len(clean_tags) <= 5:
            breakdown["hashtag_relevance"] += 10.0
        elif len(clean_tags) > 5:
            breakdown["hashtag_relevance"] += 5.0
            warnings.append(f"{len(clean_tags)} hashtags found. Instagram discovery prioritizes 3-5 focused hashtags.")
        elif clean_tags:
            breakdown["hashtag_relevance"] += 6.0
        else:
            missing_opportunities.append("Add 3-5 targeted hashtags.")

        # 6. CTA & Engagement (10 pts)
        if cta or any(w in c_lower for w in ["save", "share", "tag", "comment", "send"]):
            breakdown["audience_search_intent"] += 10.0
        else:
            missing_opportunities.append("Include an action prompt (e.g. 'Share this with your duo' or 'Save for later').")

        # 7. On-Screen / Subtitle Alignment (5 pts)
        if onscreen_text:
            breakdown["metadata_completeness"] += 5.0
        else:
            breakdown["metadata_completeness"] += 2.5
            recommendations.append("Specify on-screen text hook to catch sound-off scrollers.")

        total = sum(breakdown.values())
        return {
            "platform": "instagram",
            "overall_score": round(min(100.0, total), 1),
            "breakdown": {k: round(v, 1) for k, v in breakdown.items()},
            "warnings": warnings,
            "missing_opportunities": missing_opportunities,
            "recommendations": recommendations,
            "scoring_engine_version": "2.0.0"
        }

    def score_facebook_seo(
        self,
        description: str,
        primary_keyword: str,
        hashtags: List[str],
        game_name: str,
        game_mismatch: bool,
        cta: str = ""
    ) -> Dict[str, Any]:
        """Calculates Facebook Reels SEO quality score."""
        breakdown = {
            "keyword_relevance": 0.0,
            "gameplay_accuracy": 0.0,
            "title_quality": 0.0,
            "description_relevance": 0.0,
            "hashtag_relevance": 0.0,
            "audience_search_intent": 0.0,
            "metadata_completeness": 0.0,
        }
        warnings: List[str] = []
        recommendations: List[str] = []
        missing_opportunities: List[str] = []

        d_lower = (description or "").lower()

        # 1. Keyword Relevance (25 pts)
        if primary_keyword and primary_keyword.lower() in d_lower:
            breakdown["keyword_relevance"] += 20.0
        else:
            breakdown["keyword_relevance"] += 10.0

        if game_name and game_name.lower() in d_lower:
            breakdown["keyword_relevance"] += 5.0

        # 2. Gameplay Accuracy (20 pts)
        if game_mismatch:
            warnings.append("Conflict detected with user-supplied game name.")
            breakdown["gameplay_accuracy"] += 5.0
        else:
            breakdown["gameplay_accuracy"] += 20.0

        # 3. Hook & Opening (15 pts)
        lines = [l.strip() for l in (description or "").split("\n") if l.strip()]
        if lines and 15 <= len(lines[0]) <= 85:
            breakdown["title_quality"] += 15.0
        else:
            breakdown["title_quality"] += 8.0

        # 4. Description Flow (15 pts)
        if len(description or "") >= 70:
            breakdown["description_relevance"] += 15.0
        else:
            breakdown["description_relevance"] += 7.0

        # 5. Hashtag Discipline (10 pts)
        clean_tags = [h for h in hashtags if h.startswith("#")]
        if 2 <= len(clean_tags) <= 4:
            breakdown["hashtag_relevance"] += 10.0
        elif len(clean_tags) > 4:
            breakdown["hashtag_relevance"] += 6.0
            warnings.append("Facebook performs best with 2-3 focused hashtags.")
        else:
            breakdown["hashtag_relevance"] += 4.0

        # 6. Shareability & CTA (10 pts)
        if cta or any(w in d_lower for w in ["share", "who", "react", "friends", "tag"]):
            breakdown["audience_search_intent"] += 10.0
        else:
            missing_opportunities.append("Add a community discussion hook or share CTA.")

        # 7. Metadata Completeness (5 pts)
        breakdown["metadata_completeness"] += 5.0

        total = sum(breakdown.values())
        return {
            "platform": "facebook",
            "overall_score": round(min(100.0, total), 1),
            "breakdown": {k: round(v, 1) for k, v in breakdown.items()},
            "warnings": warnings,
            "missing_opportunities": missing_opportunities,
            "recommendations": recommendations,
            "scoring_engine_version": "2.0.0"
        }

seo_scorer = SEOScorer()
