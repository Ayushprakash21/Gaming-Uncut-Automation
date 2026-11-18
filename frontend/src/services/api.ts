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

/**
 * Safely parses response body checking Content-Type header.
 * Prevents "Unexpected token 'I', 'Internal S'... is not valid JSON" errors.
 */
async function parseResponseBody(res: Response): Promise<any> {
  const contentType = (res.headers.get('content-type') || '').toLowerCase()
  if (contentType.includes('application/json')) {
    try {
      const text = await res.text()
      if (!text || !text.trim()) return null
      return JSON.parse(text)
    } catch {
      return null
    }
  }

  // Non-JSON response (e.g. text/plain, text/html)
  try {
    const text = await res.text()
    return text ? text.trim() : null
  } catch {
    return null
  }
}

/**
 * Standardized API client that handles JSON and non-JSON responses safely,
 * parses structured backend errors (standardized { success, error, detail }),
 * and guarantees meaningful error messages across 400, 401, 403, 404, 405, 422, 429, 500.
 */
export async function safeFetch<T = any>(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<T> {
  let res: Response
  try {
    res = await fetch(input, init)
  } catch (netErr: any) {
    throw new Error(`Network error: ${netErr?.message || 'Unable to reach backend server.'}`)
  }

  const body = await parseResponseBody(res)

  if (!res.ok) {
    let errMsg = ''
    if (body && typeof body === 'object') {
      if (body.error && body.error.message) {
        errMsg = body.error.message
      } else if (body.detail) {
        if (typeof body.detail === 'string') {
          errMsg = body.detail
        } else if (Array.isArray(body.detail)) {
          errMsg = body.detail.map((d: any) => d.msg || (typeof d === 'string' ? d : JSON.stringify(d))).join(', ')
        } else {
          errMsg = JSON.stringify(body.detail)
        }
      } else if (body.message) {
        errMsg = body.message
      }
    } else if (typeof body === 'string' && body.length > 0) {
      if (body.length > 250 || body.startsWith('<!DOCTYPE') || body.startsWith('<html')) {
        errMsg = `Server error (${res.status}): Request could not be processed.`
      } else {
        errMsg = body
      }
    }

    if (!errMsg) {
      switch (res.status) {
        case 400: errMsg = 'Bad Request: Please check your input parameters.'; break;
        case 401: errMsg = 'Unauthorized: Authentication required.'; break;
        case 403: errMsg = 'Forbidden: You do not have permission for this action.'; break;
        case 404: errMsg = 'Not Found: The requested resource was not found.'; break;
        case 405: errMsg = 'Method Not Allowed: Request method is not supported.'; break;
        case 422: errMsg = 'Validation Error: Invalid data submitted.'; break;
        case 429: errMsg = 'Rate Limit Exceeded: Daily or request quota limit reached.'; break;
        case 500: errMsg = 'Internal Server Error: An unexpected server error occurred.'; break;
        case 502: errMsg = 'Bad Gateway: Upstream server error.'; break;
        case 503: errMsg = 'Service Unavailable: Server is temporarily unavailable.'; break;
        case 504: errMsg = 'Gateway Timeout: Server timed out processing request.'; break;
        default: errMsg = `Request failed with HTTP status ${res.status}.`; break;
      }
    }

    const err = new Error(errMsg) as any
    err.status = res.status
    err.data = body
    throw err
  }

  return body as T
}

export async function fetchOverview(): Promise<OverviewData> {
  return safeFetch<OverviewData>(`${API_BASE}/overview`)
}

export async function validateSource(url: string, game_name?: string) {
  return safeFetch(`${API_BASE}/reels/validate-source`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, game_name: game_name?.trim() || undefined })
  })
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
  return safeFetch(`${API_BASE}/reels/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function fetchJobProgress(jobId: number) {
  return safeFetch(`${API_BASE}/jobs/${jobId}`)
}

export async function updateClipMetadata(clipId: number, data: { title: string; topic?: string; description?: string; hashtags?: string; detected_game?: string }) {
  return safeFetch(`${API_BASE}/clips/${clipId}/metadata`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function reanalyzeClip(clipId: number) {
  return safeFetch(`${API_BASE}/clips/${clipId}/reanalyze`, {
    method: 'POST'
  })
}

export async function addClipToQueue(clipId: number, data: { platforms: string[] }) {
  return safeFetch(`${API_BASE}/clips/${clipId}/queue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function createReel(data: {
  url: string;
  template: string;
  publish_mode: string;
  platforms: string[];
  notes?: string;
}) {
  return safeFetch(`${API_BASE}/reels/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function fetchJobs(): Promise<ProcessingJobItem[]> {
  return safeFetch<ProcessingJobItem[]>(`${API_BASE}/jobs`)
}

export async function fetchClips(): Promise<GeneratedClipItem[]> {
  return safeFetch<GeneratedClipItem[]>(`${API_BASE}/clips`)
}

export async function approveClip(id: number) {
  return safeFetch(`${API_BASE}/clips/${id}/approve`, { method: 'POST' })
}

export async function rejectClip(id: number) {
  return safeFetch(`${API_BASE}/clips/${id}/reject`, { method: 'POST' })
}

export async function fetchPublishingQueue(): Promise<PublishingQueueItem[]> {
  return safeFetch<PublishingQueueItem[]>(`${API_BASE}/publishing/queue`)
}

export async function dispatchPublishingJob(jobId: number, allowRepublish: boolean = false) {
  return safeFetch(`${API_BASE}/publishing/dispatch/${jobId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ allow_republish: allowRepublish })
  })
}

export async function fetchPublishingDiagnostics(jobId: number): Promise<any> {
  return safeFetch<any>(`${API_BASE}/publishing/diagnostics/${jobId}`)
}

export async function fetchConnections(): Promise<ConnectionsData> {
  return safeFetch<ConnectionsData>(`${API_BASE}/settings/connections`)
}

export async function updateConnections(data: {
  gemini_api_key?: string;
  meta_app_id?: string;
  meta_app_secret?: string;
  meta_page_token?: string;
  meta_page_id?: string;
  meta_ig_id?: string;
}) {
  return safeFetch(`${API_BASE}/settings/connections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function importGoogleSecrets(path: string) {
  return safeFetch(`${API_BASE}/settings/import-google-secrets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path })
  })
}

export async function connectYouTubeAccount(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/youtube/connect/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function startYouTubeOAuth(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/youtube/connect/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function getYouTubeOAuthStatus(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/youtube/connect/status`)
}

export async function cancelYouTubeOAuth(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/youtube/connect/cancel`, {
    method: 'POST'
  })
}

export async function testYouTubeConnection(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/youtube/test`, {
    method: 'POST'
  })
}

export async function disconnectYouTubeAccount(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/youtube/disconnect`, {
    method: 'POST'
  })
}

export async function fetchYouTubeChannel(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/youtube/channel`)
}

export async function fetchLogs(level?: string, module?: string): Promise<SystemLogItem[]> {
  const params = new URLSearchParams()
  if (level && level !== 'ALL') params.append('level', level)
  if (module && module.trim()) params.append('module', module.trim())
  const qs = params.toString() ? `?${params.toString()}` : ''
  return safeFetch<SystemLogItem[]>(`${API_BASE}/logs${qs}`)
}

export async function fetchSystemLimits(): Promise<SystemLimits> {
  return safeFetch<SystemLimits>(`${API_BASE}/settings/limits`)
}

export async function updateSystemLimits(data: Partial<SystemLimits>): Promise<any> {
  return safeFetch(`${API_BASE}/settings/limits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function testFacebookConnection(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/facebook/test`, {
    method: 'POST'
  })
}

export async function testInstagramConnection(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/instagram/test`, {
    method: 'POST'
  })
}

export async function fetchMetaHealth(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/meta/health`)
}

export async function verifyMetaConnection(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/meta/verify`, {
    method: 'POST'
  })
}

export async function refreshMetaToken(): Promise<any> {
  return safeFetch(`${API_BASE}/settings/meta/refresh`, {
    method: 'POST'
  })
}

export async function retryJob(jobId: number): Promise<any> {
  return safeFetch(`${API_BASE}/jobs/${jobId}/retry`, {
    method: 'POST',
  })
}

export async function cancelJob(jobId: number): Promise<any> {
  return safeFetch(`${API_BASE}/jobs/${jobId}/cancel`, {
    method: 'POST',
  })
}

export async function dismissJob(jobId: number): Promise<any> {
  return safeFetch(`${API_BASE}/jobs/${jobId}/dismiss`, {
    method: 'POST',
  })
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
  return safeFetch(`${API_BASE}/discovery/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params)
  })
}

export async function createClipsFromDiscovery(payload: {
  video_ids?: number[];
  videos?: any[];
  local_file_paths?: Record<string, string>;
  target_duration?: number;
  game_name?: string;
}): Promise<{
  success: boolean;
  queued_count: number;
  queued_jobs: Array<{
    video_id: number;
    youtube_video_id: string;
    job_id: number | null;
    title: string;
    channel_title: string;
    game_title: string;
    status: string;
    error?: string;
  }>;
  message: string;
}> {
  return safeFetch(`${API_BASE}/discovery/create-clips`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}

export async function importDiscoveredVideos(videos: any[], searchId?: number): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ videos, search_id: searchId })
  })
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
  return safeFetch(`${API_BASE}/discovery/queue?${params.toString()}`)
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
  return safeFetch(`${API_BASE}/discovery/queue/${videoId}/authorize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
}

export async function processDiscoveredVideo(
  videoId: number,
  localFilePath?: string,
  autoAuthorize: boolean = true
): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/queue/${videoId}/process`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ local_file_path: localFilePath, auto_authorize: autoAuthorize })
  })
}

export async function retryDiscoveredVideo(videoId: number): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/queue/${videoId}/retry`, {
    method: 'POST'
  })
}

export async function skipDiscoveredVideo(videoId: number): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/queue/${videoId}/skip`, {
    method: 'POST'
  })
}

export async function fetchDiscoverySources(): Promise<DiscoverySourceItem[]> {
  return safeFetch<DiscoverySourceItem[]>(`${API_BASE}/discovery/sources`)
}

export async function saveDiscoverySource(source: Partial<DiscoverySourceItem>): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/sources`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(source)
  })
}

export async function deleteDiscoverySource(sourceId: number): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/sources/${sourceId}`, {
    method: 'DELETE'
  })
}

export async function fetchDiscoveryChannels(): Promise<DiscoveryChannelItem[]> {
  return safeFetch<DiscoveryChannelItem[]>(`${API_BASE}/discovery/channels`)
}

export async function saveDiscoveryChannel(channel: Partial<DiscoveryChannelItem>): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/channels`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(channel)
  })
}

export async function deleteDiscoveryChannel(channelId: string | number): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/channels/${channelId}`, {
    method: 'DELETE'
  })
}

export async function fetchDiscoverySchedule(): Promise<DiscoveryScheduleData> {
  return safeFetch<DiscoveryScheduleData>(`${API_BASE}/discovery/schedule`)
}

export async function updateDiscoverySchedule(schedule: Partial<DiscoveryScheduleData>): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/schedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(schedule)
  })
}

export async function triggerDiscoveryScanNow(): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/schedule/scan-now`, {
    method: 'POST'
  })
}

export async function pauseDiscoverySchedule(): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/schedule/pause`, {
    method: 'POST'
  })
}

export async function resumeDiscoverySchedule(): Promise<any> {
  return safeFetch(`${API_BASE}/discovery/schedule/resume`, {
    method: 'POST'
  })
}

// =========================================================================
// AI SEO & Growth Intelligence Engine Client
// =========================================================================

export async function fetchSeoDashboard(): Promise<any> {
  return safeFetch(`${API_BASE}/seo/dashboard`)
}

export async function fetchClipSeo(clipId: number): Promise<any> {
  return safeFetch(`${API_BASE}/seo/clips/${clipId}`)
}

export async function generateClipSeo(
  clipId: number,
  data?: { platforms?: string[]; bypass_cache?: boolean; custom_keywords?: string[] }
): Promise<any> {
  return safeFetch(`${API_BASE}/seo/clips/${clipId}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {})
  })
}

export async function approveClipSeo(clipId: number, platform: string = 'all'): Promise<any> {
  return safeFetch(`${API_BASE}/seo/clips/${clipId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ platform })
  })
}

export async function bulkGenerateSeo(data: {
  clip_ids?: number[];
  platforms?: string[];
  bypass_cache?: boolean;
}): Promise<any> {
  return safeFetch(`${API_BASE}/seo/bulk/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function bulkApproveSeo(data: { clip_ids: number[]; platform?: string }): Promise<any> {
  return safeFetch(`${API_BASE}/seo/bulk/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  })
}

export async function researchKeywords(gameName: string, eventType: string = ''): Promise<any> {
  const params = new URLSearchParams({ game_name: gameName, event_type: eventType })
  return safeFetch(`${API_BASE}/seo/keywords/research?${params}`)
}

export async function fetchGrowthIntelligence(): Promise<any> {
  return safeFetch(`${API_BASE}/seo/growth/intelligence`)
}

export async function computeGrowthInsights(): Promise<any> {
  return safeFetch(`${API_BASE}/seo/growth/compute-insights`, {
    method: 'POST'
  })
}

export async function fetchSystemDiagnostics(): Promise<any> {
  return safeFetch(`${API_BASE}/diagnostics/system`)
}

