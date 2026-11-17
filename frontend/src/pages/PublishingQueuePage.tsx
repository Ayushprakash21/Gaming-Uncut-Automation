import React, { useEffect, useState } from 'react'
import { CheckCircle2, AlertCircle, Clock, ExternalLink, RotateCw, Youtube, Instagram, Facebook } from 'lucide-react'
import { fetchPublishingQueue, dispatchPublishingJob } from '../services/api'
import { PublishingQueueItem } from '../types'

export const PublishingQueuePage: React.FC = () => {
  const [items, setItems] = useState<PublishingQueueItem[]>([])
  const [loading, setLoading] = useState(false)

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
    await dispatchPublishingJob(jobId)
    setTimeout(load, 1000)
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold font-brand tracking-wide text-slate-100 flex items-center space-x-2">
            <span>MULTI-PLATFORM PUBLISHING QUEUE</span>
          </h2>
          <p className="text-xs text-slate-400">
            Real-time status for YouTube Shorts, Instagram Reels, and Facebook Reels official API dispatch.
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
                <th className="p-3.5">Clip Title</th>
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
                const isUploading = item.status === 'uploading'
                const isFailed = item.status === 'failed'

                return (
                  <tr key={item.id} className="hover:bg-slate-900/40 transition">
                    <td className="p-3.5 font-medium line-clamp-1 max-w-xs">{item.title}</td>
                    <td className="p-3.5">
                      <div className="flex items-center space-x-1.5 capitalize font-medium">
                        {getPlatformIcon(item.platform)}
                        <span>{item.platform}</span>
                      </div>
                    </td>
                    <td className="p-3.5">
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider ${
                          isPublished
                            ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                            : isUploading
                            ? 'bg-purple-500/10 text-purple-300 border border-purple-500/30 animate-pulse'
                            : isFailed
                            ? 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                            : 'bg-slate-800 text-slate-400'
                        }`}
                      >
                        {item.status}
                      </span>
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
                          className="text-purple-400 hover:text-purple-300 underline font-medium"
                        >
                          Publish Now
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
