import React, { useEffect, useState } from 'react'
import { CheckCircle2, AlertCircle, Clock, ExternalLink, RotateCw, Youtube, Instagram, Facebook, Eye, ShieldAlert, Sparkles, RefreshCw, AlertTriangle } from 'lucide-react'
import { fetchPublishingQueue, dispatchPublishingJob } from '../services/api'
import { PublishingQueueItem } from '../types'

export const PublishingQueuePage: React.FC = () => {
  const [items, setItems] = useState<PublishingQueueItem[]>([])
  const [loading, setLoading] = useState(false)
  const [expandedItemId, setExpandedItemId] = useState<number | null>(null)
  const [dispatching, setDispatching] = useState<Record<number, boolean>>({})
  const [notice, setNotice] = useState<{ message: string; type: 'info' | 'error' } | null>(null)
  const [republishModal, setRepublishModal] = useState<{ jobId: number; title: string; platform: string } | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const data = await fetchPublishingQueue()
      setItems(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [])

  const isJobInFlight = (jobId: number) => {
    if (dispatching[jobId]) return true
    return items.some((i) => {
      if (i.job_id !== jobId) return false
      const s = (i.status || '').toLowerCase()
      const js = (i.job_status || '').toLowerCase()
      const inFlightStatuses = [
        'claimed', 'uploading', 'processing', 'publishing',
        'video_transferring', 'upload_initialized', 'transfer_completed',
        'facebook_processing', 'instagram_processing'
      ]
      return inFlightStatuses.includes(s) || ['claimed', 'uploading', 'processing', 'publishing'].includes(js)
    })
  }

  const handleForceDispatch = async (jobId: number, allowRepublish: boolean = false) => {
    if (dispatching[jobId]) return
    setDispatching((prev) => ({ ...prev, [jobId]: true }))
    setNotice(null)
    try {
      await dispatchPublishingJob(jobId, allowRepublish)
      setNotice({ message: `Publishing initiated for Job #${jobId}`, type: 'info' })
    } catch (err: any) {
      const msg = err?.data?.detail || err?.message || 'Publishing dispatch failed.'
      setNotice({ message: String(msg), type: 'error' })
    } finally {
      setTimeout(() => {
        setDispatching((prev) => ({ ...prev, [jobId]: false }))
        load()
      }, 1500)
    }
  }

  const getPlatformIcon = (platform: string) => {
    switch (platform) {
      case 'youtube':
        return <Youtube className="w-4 h-4 text-red-500" />
      case 'instagram':
        return <Instagram className="w-4 h-4 text-pink-500" />
      case 'facebook':
        return <Facebook className="w-4 h-4 text-blue-500" />
      default:
        return null
    }
  }

  const getStatusBadge = (status: string, approvalStatus?: string) => {
    if (approvalStatus === 'pending_review' && status === 'pending') {
      return (
        <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-amber-500/10 text-amber-300 border border-amber-500/30 flex items-center space-x-1">
          <ShieldAlert className="w-3 h-3 text-amber-400" />
          <span>Review Required</span>
        </span>
      )
    }

    const s = (status || '').toLowerCase()
    switch (s) {
      case 'published':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">Published</span>
      case 'claimed':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-indigo-500/10 text-indigo-300 border border-indigo-500/30 animate-pulse">Claimed</span>
      case 'upload_initialized':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-cyan-500/10 text-cyan-300 border border-cyan-500/30">Session Started</span>
      case 'video_transferring':
      case 'uploading':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-purple-500/10 text-purple-300 border border-purple-500/30 animate-pulse">Transferring Video</span>
      case 'transfer_completed':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-blue-500/10 text-blue-300 border border-blue-500/30">Transfer Completed</span>
      case 'facebook_processing':
      case 'instagram_processing':
      case 'processing':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-amber-500/10 text-amber-300 border border-amber-500/30 animate-pulse">Processing</span>
      case 'publishing':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-indigo-500/10 text-indigo-300 border border-indigo-500/30 animate-pulse">Publishing</span>
      case 'paused_quota':
      case 'paused':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-amber-500/10 text-amber-400 border border-amber-500/30">Paused (Quota/Limit)</span>
      case 'paused_unauthorized':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-slate-800 text-slate-400 border border-slate-700">Unauthorized</span>
      case 'unknown':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-yellow-500/10 text-yellow-300 border border-yellow-500/30">Reconciling (Unknown)</span>
      case 'failed':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-rose-500/10 text-rose-400 border border-rose-500/30">Failed</span>
      default:
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-slate-800 text-slate-400 border border-slate-700">{status}</span>
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold font-brand tracking-wide text-slate-100 flex items-center space-x-2">
            <span>MULTI-PLATFORM PUBLISHING QUEUE</span>
          </h2>
          <p className="text-xs text-slate-400">
            Real-time status for YouTube Shorts, Instagram Reels, and Facebook Reels official API dispatch with AI content intelligence.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs px-3 py-1.5 rounded-lg flex items-center space-x-1.5 transition"
        >
          <RotateCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Notice Banner */}
      {notice && (
        <div className={`p-3 rounded-xl border flex items-center justify-between text-xs ${
          notice.type === 'error'
            ? 'bg-rose-950/40 border-rose-800/60 text-rose-300'
            : 'bg-emerald-950/40 border-emerald-800/60 text-emerald-300'
        }`}>
          <div className="flex items-center space-x-2">
            {notice.type === 'error' ? (
              <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
            ) : (
              <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
            )}
            <span>{notice.message}</span>
          </div>
          <button
            onClick={() => setNotice(null)}
            className="text-slate-400 hover:text-slate-200 text-xs px-2 py-0.5"
          >
            ✕
          </button>
        </div>
      )}

      {/* Republish Confirmation Modal */}
      {republishModal && (
        <div className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4">
          <div className="bg-[#111827] border border-amber-500/40 rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-4">
            <div className="flex items-center space-x-3 text-amber-400">
              <AlertTriangle className="w-6 h-6 flex-shrink-0" />
              <h3 className="text-base font-bold text-slate-100">Confirm Republish</h3>
            </div>
            <p className="text-xs text-slate-300 leading-relaxed">
              This clip has already been marked as published to <strong>{republishModal.platform}</strong>.
              Republishing will initiate a new upload session and create a duplicate post on the platform.
            </p>
            <div className="p-3 rounded-lg bg-slate-900 border border-slate-800 text-xs text-slate-300 font-medium">
              "{republishModal.title}"
            </div>
            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                onClick={() => setRepublishModal(null)}
                className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-300 transition"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  const target = republishModal
                  setRepublishModal(null)
                  handleForceDispatch(target.jobId, true)
                }}
                className="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-amber-600 hover:bg-amber-500 text-white transition flex items-center space-x-1.5"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Confirm & Republish</span>
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="bg-[#111827] border border-gaming-border rounded-2xl overflow-hidden shadow-xl">
        {items.length === 0 ? (
          <div className="p-12 text-center text-slate-500 text-xs">
            No items in publishing queue. Create a reel to queue posts.
          </div>
        ) : (
          <table className="w-full text-left text-xs text-slate-300">
            <thead className="bg-[#0e1320] text-slate-400 font-semibold border-b border-gaming-border">
              <tr>
                <th className="p-3.5">Clip / AI Intelligence</th>
                <th className="p-3.5">Platform</th>
                <th className="p-3.5">Status</th>
                <th className="p-3.5">Attempts</th>
                <th className="p-3.5">Scheduled / Uploaded</th>
                <th className="p-3.5 text-right">Action / Link</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gaming-border">
              {items.map((item) => {
                const isPublished = item.status === 'published'
                const isExpanded = expandedItemId === item.id
                const isReviewRequired = item.approval_status === 'pending_review' && item.status === 'pending'

                return (
                  <React.Fragment key={item.id}>
                    <tr className="hover:bg-slate-900/40 transition">
                      <td className="p-3.5 max-w-sm">
                        <div className="font-medium text-slate-200 line-clamp-1">{item.title}</div>
                        
                        {/* Game and Event Telemetry Badges */}
                        <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                          {item.detected_game && (
                            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded border ${
                              (item.game_confidence || 0) >= 0.70
                                ? 'bg-purple-950/60 text-purple-300 border-purple-700/50'
                                : 'bg-slate-800 text-slate-400 border-slate-700'
                            }`}>
                              🎮 {item.detected_game}
                              {(item.game_confidence || 0) > 0 && ` (${Math.round((item.game_confidence || 0) * 100)}%)`}
                            </span>
                          )}

                          {item.gameplay_summary && (
                            <span className="text-[10px] font-medium px-2 py-0.5 rounded bg-cyan-950/40 text-cyan-300 border border-cyan-800/30">
                              ⚡ {item.gameplay_summary}
                            </span>
                          )}

                          {item.file_size_mb !== undefined && item.file_size_mb !== null && (
                            <span className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
                              item.file_size_mb > 60
                                ? 'bg-rose-950/60 text-rose-300 border-rose-800/60'
                                : 'bg-slate-800 text-slate-300 border-slate-700'
                            }`} title={item.file_size_mb > 60 ? "Exceeds Meta 60MB limit - normalization required" : "Compliant file size"}>
                              📁 {item.file_size_mb} MB
                            </span>
                          )}

                          {item.duration_sec && (
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                              ⏱ {item.duration_sec}s
                            </span>
                          )}

                          <button
                            onClick={() => setExpandedItemId(isExpanded ? null : item.id)}
                            className="text-[10px] text-purple-400 hover:text-purple-300 font-semibold flex items-center space-x-1 ml-1"
                          >
                            <Eye className="w-3 h-3" />
                            <span>{isExpanded ? 'Hide Preview' : 'Platform Preview'}</span>
                          </button>
                        </div>

                        {/* Safety Notice if Review Required */}
                        {isReviewRequired && (
                          <div className="mt-2 text-[11px] p-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-300 flex items-start space-x-1.5">
                            <ShieldAlert className="w-3.5 h-3.5 flex-shrink-0 text-amber-400 mt-0.5" />
                            <span>
                              <strong>Auto-Publish Held:</strong> Game recognition is unconfirmed or heuristic. Review title and description before approving publication.
                            </span>
                          </div>
                        )}

                        {item.error_message && (
                          <div className="mt-1 flex items-start space-x-1.5 text-[11px] text-rose-300">
                            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 text-rose-400 mt-0.5" />
                            <span className="line-clamp-2">
                              {item.error_code && (
                                <span className="font-mono font-bold mr-1 px-1.5 py-0.2 rounded bg-rose-950/80 text-rose-300 border border-rose-800/60">
                                  #{item.error_code}
                                </span>
                              )}
                              {item.error_message}
                            </span>
                          </div>
                        )}
                      </td>
                      <td className="p-3.5">
                        <div className="flex items-center space-x-1.5 capitalize font-medium">
                          {getPlatformIcon(item.platform)}
                          <span>{item.platform}</span>
                        </div>
                      </td>
                      <td className="p-3.5">
                        {getStatusBadge(item.status, item.approval_status)}
                      </td>
                      <td className="p-3.5 text-slate-400 font-mono">{item.attempts}</td>
                      <td className="p-3.5 text-slate-400 font-mono text-[11px]">
                        {item.uploaded_at ? new Date(item.uploaded_at).toLocaleString() : 'Pending'}
                      </td>
                      <td className="p-3.5 text-right">
                        {item.external_url ? (
                          <div className="flex items-center justify-end space-x-2">
                            <a
                              href={item.external_url}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex items-center space-x-1 text-purple-400 hover:text-purple-300 font-semibold"
                            >
                              <span>View Post</span>
                              <ExternalLink className="w-3.5 h-3.5" />
                            </a>
                            <button
                              onClick={() => setRepublishModal({ jobId: item.job_id, title: item.title, platform: item.platform })}
                              disabled={isJobInFlight(item.job_id)}
                              className="text-[10px] text-slate-500 hover:text-slate-300 underline disabled:opacity-40"
                              title="Force republish this video"
                            >
                              Republish
                            </button>
                          </div>
                        ) : (
                          <button
                            onClick={() => {
                              if (isPublished) {
                                setRepublishModal({ jobId: item.job_id, title: item.title, platform: item.platform })
                              } else {
                                handleForceDispatch(item.job_id)
                              }
                            }}
                            disabled={isJobInFlight(item.job_id)}
                            className={`text-[11px] font-semibold px-2.5 py-1 rounded shadow transition ${
                              isJobInFlight(item.job_id)
                                ? 'bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700'
                                : 'bg-purple-600 hover:bg-purple-500 text-white'
                            }`}
                          >
                            {isJobInFlight(item.job_id)
                              ? 'Publishing...'
                              : isPublished
                              ? 'Republish'
                              : isReviewRequired
                              ? 'Approve & Publish'
                              : 'Publish Now'}
                          </button>
                        )}
                      </td>
                    </tr>

                    {/* Collapsible Platform Metadata Preview Row */}
                    {isExpanded && (
                      <tr className="bg-slate-900/60 border-b border-gaming-border">
                        <td colSpan={6} className="p-4 space-y-2">
                          <div className="text-[11px] font-semibold text-purple-300 flex items-center space-x-1.5">
                            <Sparkles className="w-3.5 h-3.5" />
                            <span>Platform Target Preview: {item.platform.toUpperCase()}</span>
                          </div>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs bg-[#0b0f19] p-3 rounded-xl border border-slate-800">
                            <div>
                              <div className="text-slate-400 text-[10px] uppercase font-bold tracking-wider mb-1">
                                {item.platform === 'youtube' ? 'YouTube Shorts Title (< 100 chars)' : 'Post Caption Title'}
                              </div>
                              <div className="text-slate-200 font-medium">{item.title}</div>
                            </div>
                            <div>
                              <div className="text-slate-400 text-[10px] uppercase font-bold tracking-wider mb-1">
                                Hashtags ({item.platform})
                              </div>
                              <div className="text-purple-400 font-mono text-[11px]">{item.hashtags || 'None'}</div>
                            </div>
                            <div className="md:col-span-2">
                              <div className="text-slate-400 text-[10px] uppercase font-bold tracking-wider mb-1">
                                Description / Caption Body
                              </div>
                              <div className="text-slate-300 whitespace-pre-wrap text-[11px]">{item.description || 'No description provided.'}</div>
                            </div>

                            {/* Video Specifications & Ingest Compliance */}
                            <div className="md:col-span-2 pt-2 border-t border-slate-800">
                              <div className="text-slate-400 text-[10px] uppercase font-bold tracking-wider mb-1.5 flex items-center justify-between">
                                <span>Video Asset & Meta Ingest Compliance</span>
                                {item.platform === 'instagram' && (
                                  <span className="text-[10px] text-purple-400 font-normal">
                                    Profile: 1080x1920 30FPS CFR, AAC 48kHz, &le; 60MB
                                  </span>
                                )}
                              </div>
                              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px] bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
                                <div>
                                  <span className="text-slate-500 block text-[9px] uppercase">File Size:</span>
                                  <span className={item.file_size_mb && item.file_size_mb > 60 ? "text-rose-400 font-semibold" : "text-slate-200"}>
                                    {item.file_size_mb ? `${item.file_size_mb} MB` : 'N/A'}
                                    {item.file_size_mb && item.file_size_mb > 60 ? ' (>60MB - Normalizing)' : ''}
                                  </span>
                                </div>
                                <div>
                                  <span className="text-slate-500 block text-[9px] uppercase">Duration:</span>
                                  <span className="text-slate-200">{item.duration_sec ? `${item.duration_sec}s` : 'N/A'}</span>
                                </div>
                                <div>
                                  <span className="text-slate-500 block text-[9px] uppercase">Resolution:</span>
                                  <span className="text-slate-200">{item.resolution || '1080x1920 (9:16)'}</span>
                                </div>
                                <div>
                                  <span className="text-slate-500 block text-[9px] uppercase">Ingest Normalization:</span>
                                  <span className="text-emerald-400 font-medium">Automatic (30 FPS CFR, &le; 55MB)</span>
                                </div>
                              </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
