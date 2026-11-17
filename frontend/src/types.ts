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
  created_at: string;
}

export interface PublishingQueueItem {
  id: number;
  job_id: number;
  platform: 'youtube' | 'instagram' | 'facebook';
  status: string;
  external_url?: string;
  error_message?: string;
  attempts: number;
  title: string;
  scheduled_time?: string;
  uploaded_at?: string;
}

export interface SystemLogItem {
  id: number;
  module: string;
  level: string;
  message: string;
  created_at: string;
}

export interface ConnectionsData {
  youtube: {
    client_configured: boolean;
    authenticated: boolean;
    project_id: string;
  };
  meta: {
    app_configured: boolean;
    token_configured: boolean;
    instagram_account_id: string;
  };
  gemini: {
    api_key_configured: boolean;
  };
}
