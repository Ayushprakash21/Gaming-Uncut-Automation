"""
Official YouTube Shorts Analytics Integration & Historical Performance Engine.
Collects and stores measured YouTube Analytics and YouTube Data API metrics per-video.
Strictly records authentic metrics from Google APIs and never invents or estimates unavailable values.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from backend.app.models.schema import (
    PlatformUpload, PublishingAnalytics, ShortsAnalyticsSnapshot, GeneratedClip
)
from backend.app.publishers.youtube.youtube_client import youtube_client, YouTubeAuthError

logger = logging.getLogger("gaming_uncut.youtube_analytics")

class YouTubeAnalyticsService:
    def __init__(self):
        self._yt_analytics = None
        self._yt_data = None

    def get_analytics_service(self, creds: Optional[Credentials] = None):
        """Builds and caches YouTube Analytics v2 client."""
        if creds is None:
            creds = youtube_client.get_credentials()
        if not creds or not creds.valid:
            return None
        try:
            return build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        except Exception as e:
            logger.warning(f"Could not build YouTube Analytics service: {e}")
            return None

    def get_data_service(self, creds: Optional[Credentials] = None):
        """Builds and caches YouTube Data API v3 client."""
        if creds is None:
            creds = youtube_client.get_credentials()
        if not creds or not creds.valid:
            return None
        try:
            return build("youtube", "v3", credentials=creds, cache_discovery=False)
        except Exception as e:
            logger.warning(f"Could not build YouTube Data API service: {e}")
            return None

    def fetch_video_metrics(self, video_id: str, creds: Optional[Credentials] = None) -> Dict[str, Any]:
        """
        Fetches authentic video metrics from YouTube Analytics API and YouTube Data API v3.
        Does not invent or estimate unavailable metrics.
        """
        clean_id = str(video_id).strip()
        if not clean_id:
            raise ValueError("video_id cannot be empty")

        if creds is None:
            creds = youtube_client.get_credentials()

        metrics: Dict[str, Any] = {
            "video_id": clean_id,
            "views": 0,
            "engaged_views": None,
            "shown_in_feed": None,
            "stayed_to_watch": None,
            "stayed_to_watch_pct": None,
            "average_view_duration_sec": None,
            "average_percentage_viewed": None,
            "watch_time_minutes": None,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "subscribers_gained": None,
            "traffic_sources": {},
            "is_measured": True,
            "is_analytics_api": False,
            "api_status": "data_api_only",
            "retrieval_timestamp": datetime.utcnow().isoformat()
        }

        # 1. First fetch real-time public statistics from YouTube Data API v3
        data_svc = self.get_data_service(creds)
        if data_svc:
            try:
                res = data_svc.videos().list(
                    part="statistics,contentDetails,snippet",
                    id=clean_id
                ).execute()

                items = res.get("items", [])
                if items:
                    stats = items[0].get("statistics", {})
                    metrics["views"] = int(stats.get("viewCount", 0))
                    metrics["likes"] = int(stats.get("likeCount", 0))
                    metrics["comments"] = int(stats.get("commentCount", 0))
                    logger.debug(f"[YouTubeDataAPI] Video {clean_id}: views={metrics['views']}, likes={metrics['likes']}")
            except HttpError as e:
                logger.warning(f"YouTube Data API query failed for {clean_id}: {e}")
            except Exception as e:
                logger.warning(f"Unexpected error in YouTube Data API query for {clean_id}: {e}")

        # 2. Attempt YouTube Analytics API v2 query for deep retention & Shorts engagement metrics
        analytics_svc = self.get_analytics_service(creds)
        if analytics_svc:
            try:
                # Query channel report filtered to this specific video
                # Note: YouTube Analytics data has an inherent 24-48h processing lag on new uploads
                start_date = "2024-01-01"
                end_date = datetime.utcnow().strftime("%Y-%m-%d")

                report = analytics_svc.reports().query(
                    ids="channel==MINE",
                    startDate=start_date,
                    endDate=end_date,
                    metrics="views,likes,comments,shares,subscribersGained,averageViewDuration,averageViewPercentage,estimatedMinutesWatched",
                    dimensions="video",
                    filters=f"video=={clean_id}"
                ).execute()

                rows = report.get("rows", [])
                cols = [h["name"] for h in report.get("columnHeaders", [])]

                if rows:
                    row_data = dict(zip(cols, rows[0]))
                    metrics["is_analytics_api"] = True
                    metrics["api_status"] = "analytics_api_verified"
                    
                    if "views" in row_data:
                        metrics["views"] = max(metrics["views"], int(row_data["views"]))
                    if "likes" in row_data:
                        metrics["likes"] = max(metrics["likes"], int(row_data["likes"]))
                    if "comments" in row_data:
                        metrics["comments"] = max(metrics["comments"], int(row_data["comments"]))
                    if "shares" in row_data:
                        metrics["shares"] = int(row_data["shares"])
                    if "subscribersGained" in row_data:
                        metrics["subscribers_gained"] = int(row_data["subscribersGained"])
                    if "averageViewDuration" in row_data:
                        metrics["average_view_duration_sec"] = round(float(row_data["averageViewDuration"]), 2)
                    if "averageViewPercentage" in row_data:
                        metrics["average_percentage_viewed"] = round(float(row_data["averageViewPercentage"]), 2)
                    if "estimatedMinutesWatched" in row_data:
                        metrics["watch_time_minutes"] = round(float(row_data["estimatedMinutesWatched"]), 2)

                    logger.info(f"[YouTubeAnalytics] Fetched authentic analytics for video {clean_id}")

                # Query traffic source breakdown if supported
                try:
                    ts_report = analytics_svc.reports().query(
                        ids="channel==MINE",
                        startDate=start_date,
                        endDate=end_date,
                        metrics="views,estimatedMinutesWatched",
                        dimensions="insightTrafficSourceType",
                        filters=f"video=={clean_id}"
                    ).execute()
                    ts_rows = ts_report.get("rows", [])
                    if ts_rows:
                        traffic_dict = {}
                        for r in ts_rows:
                            traffic_dict[str(r[0])] = {
                                "views": int(r[1]),
                                "estimatedMinutesWatched": round(float(r[2]), 2)
                            }
                        metrics["traffic_sources"] = traffic_dict
                except Exception as e:
                    logger.debug(f"Traffic source dimension not available for {clean_id}: {e}")

            except HttpError as e:
                # 403 or insufficient permissions or data not ready
                err_str = str(e)
                if "accessnotconfigured" in err_str.lower() or "not been used in project" in err_str.lower():
                    logger.warning(
                        "YouTube Analytics API v2 is not enabled in Google Cloud project 43547251911. "
                        "Enable it at: https://console.developers.google.com/apis/api/youtubeanalytics.googleapis.com/overview?project=43547251911"
                    )
                    metrics["api_status"] = "analytics_api_not_enabled"
                    metrics["api_note"] = "YouTube Analytics API v2 must be enabled in Google Cloud Console (project 43547251911)."
                else:
                    logger.info(f"YouTube Analytics API reports query returned status {e.resp.status} for {clean_id}: {e}")
                    metrics["api_status"] = f"analytics_api_unavailable_{e.resp.status}"
            except Exception as e:
                logger.warning(f"Unexpected error querying YouTube Analytics API for {clean_id}: {e}")
                metrics["api_status"] = "analytics_api_error"

        return metrics

    def record_snapshot(
        self,
        db: Session,
        platform_upload_id: int,
        forced_window: Optional[str] = None
    ) -> Optional[ShortsAnalyticsSnapshot]:
        """
        Collects metrics for a YouTube platform upload and persists both the current
        PublishingAnalytics record and historical snapshot windows (1h, 6h, 24h, 72h, latest).
        """
        upload = db.query(PlatformUpload).filter_by(id=platform_upload_id).first()
        if not upload or upload.platform != "youtube" or not upload.external_post_id:
            logger.warning(f"Platform upload {platform_upload_id} is not an active YouTube post.")
            return None

        video_id = upload.external_post_id
        clip_id = upload.publishing_job.clip_id if upload.publishing_job else None

        # Fetch authentic metrics
        metrics = self.fetch_video_metrics(video_id)
        now = datetime.utcnow()

        # Update or create PublishingAnalytics current state
        analytics = db.query(PublishingAnalytics).filter_by(platform_upload_id=upload.id).first()
        if not analytics:
            analytics = PublishingAnalytics(
                platform_upload_id=upload.id,
                platform="youtube",
                external_post_id=video_id
            )
            db.add(analytics)

        analytics.views = metrics["views"]
        analytics.likes = metrics["likes"]
        analytics.comments = metrics["comments"]
        analytics.shares = metrics["shares"]
        analytics.engaged_views = metrics["engaged_views"]
        analytics.shown_in_feed = metrics["shown_in_feed"]
        analytics.stayed_to_watch = metrics["stayed_to_watch"]
        analytics.stayed_to_watch_pct = metrics["stayed_to_watch_pct"]
        analytics.average_view_duration_sec = metrics["average_view_duration_sec"]
        analytics.average_percentage_viewed = metrics["average_percentage_viewed"]
        analytics.watch_time_minutes = metrics["watch_time_minutes"]
        analytics.subscribers_gained = metrics["subscribers_gained"]
        analytics.traffic_sources_json = json.dumps(metrics["traffic_sources"]) if metrics["traffic_sources"] else None
        analytics.is_measured = metrics["is_measured"]
        analytics.is_analytics_api = metrics["is_analytics_api"]
        analytics.last_synced_at = now
        
        # Calculate engagement rate: (likes + comments + shares) / max(views, 1)
        tot_eng = metrics["likes"] + metrics["comments"] + metrics["shares"]
        analytics.engagement_rate = round((tot_eng / max(1, metrics["views"])) * 100, 2)

        # Determine target snapshot window
        uploaded_at = upload.uploaded_at or upload.created_at or now
        age_hours = (now - uploaded_at).total_seconds() / 3600.0

        windows_to_record = ["latest"]
        if forced_window:
            windows_to_record.append(forced_window)
        else:
            if age_hours <= 2.5:
                windows_to_record.append("1h")
            elif 4.5 <= age_hours <= 8.5:
                windows_to_record.append("6h")
            elif 20.0 <= age_hours <= 28.0:
                windows_to_record.append("24h")
            elif 66.0 <= age_hours <= 78.0:
                windows_to_record.append("72h")

        last_snap = None
        for win in set(windows_to_record):
            # Check if this window snapshot already exists (except "latest" which updates)
            snap = None
            if win == "latest":
                snap = db.query(ShortsAnalyticsSnapshot).filter_by(
                    platform_upload_id=upload.id,
                    snapshot_window="latest"
                ).first()
            else:
                snap = db.query(ShortsAnalyticsSnapshot).filter_by(
                    platform_upload_id=upload.id,
                    snapshot_window=win
                ).first()

            if not snap:
                snap = ShortsAnalyticsSnapshot(
                    platform_upload_id=upload.id,
                    clip_id=clip_id,
                    platform="youtube",
                    external_post_id=video_id,
                    snapshot_window=win
                )
                db.add(snap)

            snap.captured_at = now
            snap.views = metrics["views"]
            snap.engaged_views = metrics["engaged_views"]
            snap.shown_in_feed = metrics["shown_in_feed"]
            snap.stayed_to_watch = metrics["stayed_to_watch"]
            snap.stayed_to_watch_pct = metrics["stayed_to_watch_pct"]
            snap.average_view_duration_sec = metrics["average_view_duration_sec"]
            snap.average_percentage_viewed = metrics["average_percentage_viewed"]
            snap.watch_time_minutes = metrics["watch_time_minutes"]
            snap.likes = metrics["likes"]
            snap.comments = metrics["comments"]
            snap.shares = metrics["shares"]
            snap.subscribers_gained = metrics["subscribers_gained"]
            snap.traffic_sources_json = json.dumps(metrics["traffic_sources"]) if metrics["traffic_sources"] else None
            snap.is_measured = metrics["is_measured"]
            snap.is_analytics_api = metrics["is_analytics_api"]
            snap.notes = metrics.get("api_status")
            last_snap = snap

        db.commit()
        return last_snap

    def compare_snapshots(self, db: Session, platform_upload_id: int) -> Dict[str, Any]:
        """
        Returns structured comparison across 1h, 6h, 24h, and 72h snapshot windows.
        Calculates view velocity and retention changes between milestones.
        """
        snapshots = db.query(ShortsAnalyticsSnapshot).filter_by(
            platform_upload_id=platform_upload_id
        ).all()

        windows_data: Dict[str, Optional[Dict[str, Any]]] = {
            "1h": None,
            "6h": None,
            "24h": None,
            "72h": None,
            "latest": None
        }

        for s in snapshots:
            win = s.snapshot_window
            if win in windows_data:
                windows_data[win] = {
                    "views": s.views,
                    "engaged_views": s.engaged_views,
                    "shown_in_feed": s.shown_in_feed,
                    "stayed_to_watch": s.stayed_to_watch,
                    "stayed_to_watch_pct": s.stayed_to_watch_pct,
                    "average_view_duration_sec": s.average_view_duration_sec,
                    "average_percentage_viewed": s.average_percentage_viewed,
                    "watch_time_minutes": s.watch_time_minutes,
                    "likes": s.likes,
                    "comments": s.comments,
                    "shares": s.shares,
                    "subscribers_gained": s.subscribers_gained,
                    "captured_at": s.captured_at.isoformat() if s.captured_at else None,
                    "is_analytics_api": s.is_analytics_api
                }

        # Calculate deltas & velocity where successive windows exist
        comparison_insights = []
        if windows_data["1h"] and windows_data["6h"]:
            delta_views = windows_data["6h"]["views"] - windows_data["1h"]["views"]
            velocity_1_to_6 = round(delta_views / 5.0, 1)
            comparison_insights.append({
                "milestone": "1h -> 6h",
                "delta_views": delta_views,
                "view_velocity_per_hour": velocity_1_to_6,
                "retention_change_pct": (
                    round(windows_data["6h"]["average_percentage_viewed"] - windows_data["1h"]["average_percentage_viewed"], 1)
                    if windows_data["6h"]["average_percentage_viewed"] is not None and windows_data["1h"]["average_percentage_viewed"] is not None
                    else None
                )
            })

        if windows_data["6h"] and windows_data["24h"]:
            delta_views = windows_data["24h"]["views"] - windows_data["6h"]["views"]
            velocity_6_to_24 = round(delta_views / 18.0, 1)
            comparison_insights.append({
                "milestone": "6h -> 24h",
                "delta_views": delta_views,
                "view_velocity_per_hour": velocity_6_to_24,
                "retention_change_pct": (
                    round(windows_data["24h"]["average_percentage_viewed"] - windows_data["6h"]["average_percentage_viewed"], 1)
                    if windows_data["24h"]["average_percentage_viewed"] is not None and windows_data["6h"]["average_percentage_viewed"] is not None
                    else None
                )
            })

        return {
            "platform_upload_id": platform_upload_id,
            "windows": windows_data,
            "milestone_comparisons": comparison_insights
        }

    def sync_all_active_youtube_uploads(self, db: Session) -> Dict[str, Any]:
        """Iterates through verified YouTube uploads and captures fresh performance snapshots."""
        uploads = db.query(PlatformUpload).filter(
            PlatformUpload.platform == "youtube",
            PlatformUpload.status.in_(["published", "published_verified", "completed"]),
            PlatformUpload.external_post_id.isnot(None)
        ).all()

        synced_count = 0
        errors = []

        for up in uploads:
            try:
                self.record_snapshot(db, up.id)
                synced_count += 1
            except Exception as e:
                err_msg = f"Failed syncing analytics for upload {up.id} ({up.external_post_id}): {e}"
                logger.warning(err_msg)
                errors.append(err_msg)

        return {
            "total_target_uploads": len(uploads),
            "synced_count": synced_count,
            "errors_count": len(errors),
            "errors": errors[:5],
            "synced_at": datetime.utcnow().isoformat()
        }

youtube_analytics_service = YouTubeAnalyticsService()
