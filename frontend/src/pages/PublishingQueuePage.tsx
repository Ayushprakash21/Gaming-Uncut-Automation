import React, { useEffect, useState } from 'react'
import { CheckCircle2, AlertCircle, Clock, ExternalLink, RotateCw, Youtube, Instagram, Facebook, Eye, ShieldAlert, Sparkles } from 'lucide-react'
import { fetchPublishingQueue, dispatchPublishingJob } from '../services/api'
import { PublishingQueueItem } from '../types'

export const PublishingQueuePage: React.FC = () => {
  const [items, setItems] = useState<PublishingQueueItem[]>([])
  const [loading, setLoading] = useState(false)
  const [expandedItemId, setExpandedItemId] = useState<number | null>(null)
  const [dispatching, setDispatching] = useState<Record<number, boolean>>({})

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

  const handleForceDispatch = async (jobId: number) => {
    if (dispatching[jobId]) return
    setDispatching((prev) => ({ ...prev, [jobId]: true }))
    try {
      await dispatchPublishingJob(jobId)
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

    switch (status) {
      case 'published':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">Published</span>
      case 'upload_initialized':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-cyan-500/10 text-cyan-300 border border-cyan-500/30">Session Started</span>
      case 'video_transferring':
      case 'uploading':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-purple-500/10 text-purple-300 border border-purple-500/30 animate-pulse">Transferring Video</span>
      case 'transfer_completed':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-blue-500/10 text-blue-300 border border-blue-500/30">Transfer Completed</span>
      case 'facebook_processing':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-amber-500/10 text-amber-300 border border-amber-500/30 animate-pulse">Meta Processing</span>
      case 'publishing':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-indigo-500/10 text-indigo-300 border border-indigo-500/30 animate-pulse">Publishing</span>
      case 'paused_quota':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-amber-500/10 text-amber-400 border border-amber-500/30">Paused (Quota/Limit)</span>
      case 'paused_unauthorized':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-slate-800 text-slate-400 border border-slate-700">Unauthorized</span>
      case 'failed':
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-rose-500/10 text-rose-400 border border-rose-500/30">Failed</span>
      default:
        return <span className="text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider bg-slate-800 text-slate-400">{status}</span>
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
                          <a
                            href={item.external_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center space-x-1 text-purple-400 hover:text-purple-300 font-semibold"
                          >
                            <span>View Post</span>
                            <ExternalLink className="w-3.5 h-3.5" />
                          </a>
                        ) : (
                          <button
                            onClick={() => handleForceDispatch(item.job_id)}
                            disabled={['video_transferring', 'uploading', 'instagram_processing', 'upload_initialized', 'transfer_completed', 'publishing'].includes(item.status) || !!dispatching[item.job_id]}
                            className={`text-[11px] font-semibold px-2.5 py-1 rounded shadow transition ${
                              ['video_transferring', 'uploading', 'instagram_processing', 'upload_initialized', 'transfer_completed', 'publishing'].includes(item.status) || !!dispatching[item.job_id]
                                ? 'bg-slate-800 text-slate-400 cursor-not-allowed border border-slate-700'
                                : 'bg-purple-600 hover:bg-purple-500 text-white'
                            }`}
                          >
                            {!!dispatching[item.job_id] || ['video_transferring', 'uploading', 'instagram_processing', 'upload_initialized', 'transfer_completed', 'publishing'].includes(item.status)
                              ? 'Publishing...'
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
