export interface OverviewData {
  kpis: {
    total_videos_processed: number;
    total_clips_generated: number;
    published_count: number;
    failed_count: number;
    pending_queue: number;
    today_published: number;
    daily_target: number;
    target_progress_pct: number;
  };
  platforms: {
    youtube: { connected: boolean; quota_used_today: number };
    instagram: { connected: boolean };
    facebook: { connected: boolean };
    gemini: { connected: boolean };
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
