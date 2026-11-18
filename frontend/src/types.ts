export interface SystemLimits {
  daily_generation_target: number;
  daily_publishing_target: number;
  youtube_daily_limit: number;
  instagram_daily_limit: number;
  facebook_daily_limit: number;
  auto_reschedule_on_quota: boolean;
  updated_at?: string;
}

export interface PlatformQuotaDetail {
  published_today: number;
  user_daily_limit: number;
  quota_units_used_today?: number;
  quota_units_bucket_limit?: number;
  api_quota_exceeded?: boolean;
  channel_limit_reached?: boolean;
  rate_limited?: boolean;
  pause_until?: string | null;
  status: string;
  last_restriction_reason?: string | null;
}

export interface OverviewData {
  kpis: {
    total_videos_processed: number;
    total_clips_generated: number;
    today_generated?: number;
    today_queued?: number;
    today_published?: number;
    today_failed?: number;
    published_count: number;
    failed_count: number;
    pending_queue: number;
    daily_target: number;
    daily_generation_target?: number;
    daily_publishing_target?: number;
    generation_progress_pct?: number;
    publishing_progress_pct?: number;
    target_progress_pct: number;
  };
  platforms: {
    youtube: { connected: boolean; quota_used_today: number; status?: string; paused?: boolean };
    instagram: { connected: boolean; published_today?: number; limit?: number; paused?: boolean };
    facebook: { connected: boolean; published_today?: number; limit?: number; paused?: boolean };
    gemini: { connected: boolean };
  };
  quotas?: {
    youtube: PlatformQuotaDetail;
    instagram: PlatformQuotaDetail;
    facebook: PlatformQuotaDetail;
  };
}

export interface ProcessingJobItem {
  id: number;
  source_title: string;
  source_url: string;
  status: string;
  progress_pct: number;
  current_step: string;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  cancelled_at?: string;
  detected_game?: string;
  game_confidence?: number;
  gameplay_event?: string;
  analysis_status?: string;
  analysis_provider?: string;
}

export interface GeneratedClipItem {
  id: number;
  video_url: string;
  thumbnail_url?: string;
  duration_sec: number;
  layout_template: string;
  quality_score: number;
  validation_status: string;
  title: string;
  topic?: string;
  description?: string;
  hashtags?: string;
  detected_game?: string;
  game_confidence?: number;
  game_franchise?: string;
  game_genre?: string;
  gameplay_summary?: string;
  detected_events?: Array<{
    name: string;
    start: number;
    end: number;
    confidence?: number;
    visual_evidence?: string;
    importance_score?: number;
  }>;
  candidate_titles?: string[];
  analysis_provider?: string;
  analysis_status?: string;
  created_at: string;
}

export interface PublishingQueueItem {
  id: number;
  job_id: number;
  job_status?: string;
  platform: 'youtube' | 'instagram' | 'facebook';
  status: string;
  approval_status?: string;
  external_url?: string;
  error_code?: string;
  error_message?: string;
  attempts: number;
  title: string;
  description?: string;
  hashtags?: string;
  detected_game?: string;
  game_confidence?: number;
  gameplay_summary?: string;
  analysis_status?: string;
  analysis_provider?: string;
  thumbnail_url?: string;
  scheduled_time?: string;
  uploaded_at?: string;
}

export interface SystemLogItem {
  id: number;
  module: string;
  level: string;
  message: string;
  metadata_json?: string | null;
  created_at: string;
}

export interface VerifiedYouTubeChannel {
  channel_id: string;
  title: string;
  custom_url?: string;
  description?: string;
  thumbnail_url?: string;
  subscriber_count?: string;
  video_count?: string;
  verified_at?: string;
}

export interface MetaTokenHealth {
  configured: boolean;
  authenticated: boolean;
  status: string;
  status_label: string;
  token_type: string;
  is_valid: boolean;
  is_expired: boolean;
  is_permanent: boolean;
  expiring_soon: boolean;
  expires_at?: number | null;
  expires_at_iso?: string | null;
  expires_in_days?: number | null;
  data_access_expires_at?: number | null;
  can_refresh: boolean;
  app_id?: string;
  app_matches?: boolean;
  application_name?: string;
  page_id?: string;
  page_name?: string;
  instagram_account_id?: string;
  instagram_username?: string;
  facebook_ready: boolean;
  instagram_ready: boolean;
  facebook_status: string;
  instagram_status: string;
  permissions: string[];
  missing_facebook_scopes: string[];
  missing_instagram_scopes: string[];
  last_verified_at?: string | null;
  last_auth_error?: string | null;
}

export interface ConnectionsData {
  youtube: {
    client_configured: boolean;
    authenticated: boolean;
    connected: boolean;
    status: string;
    status_label: string;
    project_id: string;
    channel?: VerifiedYouTubeChannel | null;
  };
  instagram: {
    connected: boolean;
    status: string;
    status_label: string;
    account_id: string;
    username?: string;
  };
  facebook: {
    connected: boolean;
    status: string;
    status_label: string;
    page_id?: string;
    page_name?: string;
  };
  meta: {
    app_configured: boolean;
    token_configured: boolean;
    page_id?: string;
    instagram_account_id: string;
    health?: MetaTokenHealth;
  };
  gemini: {
    api_key_configured: boolean;
    connected: boolean;
    status_label: string;
  };
}

export interface DiscoveredVideoItem {
  id: number;
  youtube_video_id: string;
  title: string;
  channel_id?: string;
  channel_title?: string;
  published_at?: string;
  duration_sec: number;
  view_count: number;
  thumbnail_url?: string;
  youtube_url: string;
  game_title: string;
  discovery_mode: string;
  status: string;
  processing_job_id?: number | null;
  clips_count: number;
  job_progress_pct?: number;
  job_current_step?: string | null;
  job_status?: string | null;
  error_message?: string | null;
  source_alternatives?: Array<{ type: string; label: string; description: string }>;
  authorization?: {
    auth_type: string;
    authorized_by?: string;
    source_owner?: string;
    permitted_scope?: string;
    evidence_url?: string;
    confirmed_at?: string;
  } | null;
  created_at?: string;
}

export interface ManualSearchVideoResult {
  youtube_video_id: string;
  title: string;
  channel_id: string;
  channel_title: string;
  published_at?: string;
  duration_sec: number;
  duration_formatted: string;
  view_count: number;
  thumbnail_url: string;
  youtube_url: string;
  game_title: string;
  description?: string;
  in_queue?: boolean;
  queue_status?: string;
  discovered_video_id?: number | null;
  authorization_state?: string;
}

export interface DiscoverySourceItem {
  id: number;
  name: string;
  game_title: string;
  keywords: string[];
  is_active: boolean;
  min_duration_sec: number;
  max_duration_sec: number;
  min_views: number;
  max_results_per_scan: number;
  priority: number;
  last_scanned_at?: string | null;
  next_scan_at?: string | null;
}

export interface DiscoveryChannelItem {
  id: number;
  channel_id: string;
  channel_title: string;
  custom_url?: string | null;
  is_approved: boolean;
  auto_authorize_sources: boolean;
  notes?: string | null;
}

export interface DiscoveryScheduleData {
  id: number;
  name: string;
  interval_minutes: number;
  is_active: boolean;
  max_discoveries_per_scan: number;
  max_videos_processed_per_day: number;
  max_clips_generated_per_day: number;
  ai_budget_daily: number;
  auto_approve_clips: boolean;
  auto_publish: boolean;
  last_scan_at?: string | null;
  next_scan_at?: string | null;
  last_scan_status: string;
}

