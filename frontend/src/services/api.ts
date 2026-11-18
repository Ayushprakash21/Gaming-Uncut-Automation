import {
  OverviewData,
  ProcessingJobItem,
  GeneratedClipItem,
  PublishingQueueItem,
  ConnectionsData,
  SystemLogItem,
  SystemLimits,
  DiscoveredVideoItem,
  ManualSearchVideoResult,
  DiscoverySourceItem,
  DiscoveryChannelItem,
  DiscoveryScheduleData
} from '../types'

const API_BASE = '/api'

export async function fetchOverview(): Promise<OverviewData> {
  const res = await fetch(`${API_BASE}/overview`)
  return res.json()
}

export async function validateSource(url: string, game_name?: string) {
  const res = await fetch(`${API_BASE}/reels/validate-source`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, game_name: game_name?.trim() || undefined })
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Source validation failed.')
  }
  return data
}

export async function generateReel(data: {
  url: string;
  game_name?: string;
  source_authorized: boolean;
  target_duration?: number;
  resolution?: string;
  aspect_ratio?: string;
  template: string;
  ai_highlight_detection?: boolean;
  smart_cropping?: boolean;
  animated_captions?: boolean;
  watermark?: boolean;
  ai_title_generation?: boolean;
  ai_topic_classification?: boolean;
  ai_description_generation?: boolean;
  ai_hashtag_generation?: boolean;
  publish_mode: string;
  platforms: string[];
  notes?: string;
}) {
  const res = await fetch(`${API_BASE}/reels/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  const json = await res.json()
  if (!res.ok) {
    throw new Error(json.detail || 'Reel generation failed.')
  }
  return json
}

export async function fetchJobProgress(jobId: number) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`)
  return res.json()
}

export async function updateClipMetadata(clipId: number, data: { title: string; topic?: string; description?: string; hashtags?: string; detected_game?: string }) {
  const res = await fetch(`${API_BASE}/clips/${clipId}/metadata`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  return res.json()
}

export async function reanalyzeClip(clipId: number) {
  const res = await fetch(`${API_BASE}/clips/${clipId}/reanalyze`, {
    method: 'POST'
  })
  return res.json()
}

export async function addClipToQueue(clipId: number, data: { platforms: string[] }) {
  const res = await fetch(`${API_BASE}/clips/${clipId}/queue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  return res.json()
}

export async function createReel(data: {
  url: string;
  template: string;
  publish_mode: string;
  platforms: string[];
  notes?: string;
}) {
  const res = await fetch(`${API_BASE}/reels/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  return res.json()
}

export async function fetchJobs(): Promise<ProcessingJobItem[]> {
  const res = await fetch(`${API_BASE}/jobs`)
  return res.json()
}

export async function fetchClips(): Promise<GeneratedClipItem[]> {
  const res = await fetch(`${API_BASE}/clips`)
  return res.json()
}

export async function approveClip(id: number) {
  const res = await fetch(`${API_BASE}/clips/${id}/approve`, { method: 'POST' })
  return res.json()
}

export async function rejectClip(id: number) {
  const res = await fetch(`${API_BASE}/clips/${id}/reject`, { method: 'POST' })
  return res.json()
}

export async function fetchPublishingQueue(): Promise<PublishingQueueItem[]> {
  const res = await fetch(`${API_BASE}/publishing/queue`)
  return res.json()
}

export async function dispatchPublishingJob(jobId: number) {
  const res = await fetch(`${API_BASE}/publishing/dispatch/${jobId}`, { method: 'POST' })
  return res.json()
}

export async function fetchConnections(): Promise<ConnectionsData> {
  const res = await fetch(`${API_BASE}/settings/connections`)
  return res.json()
}

export async function updateConnections(data: {
  gemini_api_key?: string;
  meta_app_id?: string;
  meta_app_secret?: string;
  meta_page_token?: string;
  meta_page_id?: string;
  meta_ig_id?: string;
}) {
  const res = await fetch(`${API_BASE}/settings/connections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  return res.json()
}

export async function importGoogleSecrets(path: string) {
  const res = await fetch(`${API_BASE}/settings/import-google-secrets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path })
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to import Google secrets')
  }
  return data
}

export async function connectYouTubeAccount(): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/youtube/connect/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'YouTube authorization failed.')
  }
  return data
}

export async function startYouTubeOAuth(): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/youtube/connect/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to start YouTube authorization.')
  }
  return data
}

export async function getYouTubeOAuthStatus(): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/youtube/connect/status`)
  return res.json()
}

export async function cancelYouTubeOAuth(): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/youtube/connect/cancel`, {
    method: 'POST'
  })
  return res.json()
}

export async function testYouTubeConnection(): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/youtube/test`, {
    method: 'POST'
  })
  return res.json()
}

export async function disconnectYouTubeAccount(): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/youtube/disconnect`, {
    method: 'POST'
  })
  return res.json()
}

export async function fetchYouTubeChannel(): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/youtube/channel`)
  return res.json()
}

export async function fetchLogs(level?: string, module?: string): Promise<SystemLogItem[]> {
  const params = new URLSearchParams()
  if (level && level !== 'ALL') params.append('level', level)
  if (module && module.trim()) params.append('module', module.trim())
  const qs = params.toString() ? `?${params.toString()}` : ''
  const res = await fetch(`${API_BASE}/logs${qs}`)
  if (!res.ok) {
    throw new Error(`Failed to fetch audit logs: HTTP ${res.status} ${res.statusText}`)
  }
  return res.json()
}

export async function fetchSystemLimits(): Promise<SystemLimits> {
  const res = await fetch(`${API_BASE}/settings/limits`)
  return res.json()
}

export async function updateSystemLimits(data: Partial<SystemLimits>): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/limits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  return res.json()
}

export async function testFacebookConnection(): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/facebook/test`, {
    method: 'POST'
  })
  return res.json()
}

export async function testInstagramConnection(): Promise<any> {
  const res = await fetch(`${API_BASE}/settings/instagram/test`, {
    method: 'POST'
  })
  return res.json()
}

export async function retryJob(jobId: number): Promise<any> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/retry`, {
    method: 'POST',
  })
  const json = await res.json()
  if (!res.ok) {
    throw new Error(json.detail || 'Failed to retry job.')
  }
  return json
}

export async function cancelJob(jobId: number): Promise<any> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/cancel`, {
    method: 'POST',
  })
  const json = await res.json()
  if (!res.ok) {
    throw new Error(json.detail || 'Failed to cancel job.')
  }
  return json
}

export async function dismissJob(jobId: number): Promise<any> {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/dismiss`, {
    method: 'POST',
  })
  const json = await res.json()
  if (!res.ok) {
    throw new Error(json.detail || 'Failed to dismiss job.')
  }
  return json
}

// =========================================================================
// Video Discovery & Acquisition Engine API
// =========================================================================

export async function searchYouTubeDiscovery(params: {
  query?: string;
  game_titles?: string[];
  channel_id?: string;
  published_after?: string;
  duration_filter?: string;
  min_views?: number;
  page_token?: string;
  max_results?: number;
}): Promise<{
  items: ManualSearchVideoResult[];
  next_page_token?: string;
  prev_page_token?: string;
  total_results: number;
  search_id?: number;
  quota_used: number;
}> {
  const res = await fetch(`${API_BASE}/discovery/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params)
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'YouTube discovery search failed.')
  }
  return data
}

export async function importDiscoveredVideos(videos: any[], searchId?: number): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ videos, search_id: searchId })
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to import videos to discovery queue.')
  }
  return data
}

export async function fetchDiscoveryQueue(
  status?: string,
  search?: string,
  limit: number = 50,
  offset: number = 0
): Promise<{ total: number; items: DiscoveredVideoItem[] }> {
  const params = new URLSearchParams()
  if (status && status !== 'ALL') params.append('status', status)
  if (search && search.trim()) params.append('search', search.trim())
  params.append('limit', limit.toString())
  params.append('offset', offset.toString())
  const res = await fetch(`${API_BASE}/discovery/queue?${params.toString()}`)
  if (!res.ok) {
    throw new Error(`Failed to fetch discovery queue: HTTP ${res.status}`)
  }
  return res.json()
}

export async function authorizeDiscoveredVideo(
  videoId: number,
  payload: {
    auth_type: string;
    authorized_by?: string;
    source_owner?: string;
    permitted_scope?: string;
    evidence_url?: string;
    notes?: string;
    local_file_path?: string;
    enqueue_now?: boolean;
  }
): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/queue/${videoId}/authorize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to authorize video source.')
  }
  return data
}

export async function processDiscoveredVideo(
  videoId: number,
  localFilePath?: string
): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/queue/${videoId}/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ local_file_path: localFilePath })
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to queue video for AI processing.')
  }
  return data
}

export async function retryDiscoveredVideo(videoId: number): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/queue/${videoId}/retry`, {
    method: 'POST'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to retry video.')
  }
  return data
}

export async function skipDiscoveredVideo(videoId: number): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/queue/${videoId}/skip`, {
    method: 'POST'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to skip video.')
  }
  return data
}

export async function fetchDiscoverySources(): Promise<DiscoverySourceItem[]> {
  const res = await fetch(`${API_BASE}/discovery/sources`)
  if (!res.ok) {
    throw new Error(`Failed to fetch discovery sources: HTTP ${res.status}`)
  }
  return res.json()
}

export async function saveDiscoverySource(source: Partial<DiscoverySourceItem>): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/sources`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(source)
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to save discovery source.')
  }
  return data
}

export async function deleteDiscoverySource(sourceId: number): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/sources/${sourceId}`, {
    method: 'DELETE'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to delete discovery source.')
  }
  return data
}

export async function fetchDiscoveryChannels(): Promise<DiscoveryChannelItem[]> {
  const res = await fetch(`${API_BASE}/discovery/channels`)
  if (!res.ok) {
    throw new Error(`Failed to fetch discovery channels: HTTP ${res.status}`)
  }
  return res.json()
}

export async function saveDiscoveryChannel(channel: Partial<DiscoveryChannelItem>): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/channels`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(channel)
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to save discovery channel.')
  }
  return data
}

export async function deleteDiscoveryChannel(channelId: string | number): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/channels/${channelId}`, {
    method: 'DELETE'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to delete discovery channel.')
  }
  return data
}

export async function fetchDiscoverySchedule(): Promise<DiscoveryScheduleData> {
  const res = await fetch(`${API_BASE}/discovery/schedule`)
  if (!res.ok) {
    throw new Error(`Failed to fetch discovery schedule: HTTP ${res.status}`)
  }
  return res.json()
}

export async function updateDiscoverySchedule(schedule: Partial<DiscoveryScheduleData>): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/schedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(schedule)
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to update discovery schedule.')
  }
  return data
}

export async function triggerDiscoveryScanNow(): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/schedule/scan-now`, {
    method: 'POST'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to trigger scan.')
  }
  return data
}

export async function pauseDiscoverySchedule(): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/schedule/pause`, {
    method: 'POST'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to pause discovery schedule.')
  }
  return data
}

export async function resumeDiscoverySchedule(): Promise<any> {
  const res = await fetch(`${API_BASE}/discovery/schedule/resume`, {
    method: 'POST'
  })
  const data = await res.json()
  if (!res.ok) {
    throw new Error(data.detail || 'Failed to resume discovery schedule.')
  }
  return data
}

// =========================================================================
// AI SEO & Growth Intelligence Engine Client
// =========================================================================

export async function fetchSeoDashboard(): Promise<any> {
  const res = await fetch(`${API_BASE}/seo/dashboard`)
  if (!res.ok) throw new Error('Failed to fetch SEO dashboard.')
  return res.json()
}

export async function fetchClipSeo(clipId: number): Promise<any> {
  const res = await fetch(`${API_BASE}/seo/clips/${clipId}`)
  if (!res.ok) throw new Error(`Failed to fetch SEO metadata for Clip #${clipId}.`)
  return res.json()
}

export async function generateClipSeo(
  clipId: number,
  data?: { platforms?: string[]; bypass_cache?: boolean; custom_keywords?: string[] }
): Promise<any> {
  const res = await fetch(`${API_BASE}/seo/clips/${clipId}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {})
  })
  const json = await res.json()
  if (!res.ok) throw new Error(json.detail || 'Failed to generate SEO metadata.')
  return json
}

export async function approveClipSeo(clipId: number, platform: string = 'all'): Promise<any> {
  const res = await fetch(`${API_BASE}/seo/clips/${clipId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ platform })
  })
  const json = await res.json()
  if (!res.ok) throw new Error(json.detail || 'Failed to approve SEO metadata.')
  return json
}

export async function bulkGenerateSeo(data: {
  clip_ids?: number[];
  platforms?: string[];
  bypass_cache?: boolean;
}): Promise<any> {
  const res = await fetch(`${API_BASE}/seo/bulk/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  const json = await res.json()
  if (!res.ok) throw new Error(json.detail || 'Failed to execute bulk SEO generation.')
  return json
}

export async function bulkApproveSeo(data: { clip_ids: number[]; platform?: string }): Promise<any> {
  const res = await fetch(`${API_BASE}/seo/bulk/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
  const json = await res.json()
  if (!res.ok) throw new Error(json.detail || 'Failed to execute bulk SEO approval.')
  return json
}

export async function researchKeywords(gameName: string, eventType: string = ''): Promise<any> {
  const params = new URLSearchParams({ game_name: gameName, event_type: eventType })
  const res = await fetch(`${API_BASE}/seo/keywords/research?${params}`)
  if (!res.ok) throw new Error('Failed to research keywords.')
  return res.json()
}

export async function fetchGrowthIntelligence(): Promise<any> {
  const res = await fetch(`${API_BASE}/seo/growth/intelligence`)
  if (!res.ok) throw new Error('Failed to fetch growth intelligence.')
  return res.json()
}

export async function computeGrowthInsights(): Promise<any> {
  const res = await fetch(`${API_BASE}/seo/growth/compute-insights`, {
    method: 'POST'
  })
  const json = await res.json()
  if (!res.ok) throw new Error(json.detail || 'Failed to compute growth insights.')
  return json
}

