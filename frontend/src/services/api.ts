import { OverviewData, ProcessingJobItem, GeneratedClipItem, PublishingQueueItem, ConnectionsData, SystemLogItem } from '../types'

const API_BASE = '/api'

export async function fetchOverview(): Promise<OverviewData> {
  const res = await fetch(`${API_BASE}/overview`)
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

export async function fetchLogs(): Promise<SystemLogItem[]> {
  const res = await fetch(`${API_BASE}/logs`)
  return res.json()
}
