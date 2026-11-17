import React, { useEffect, useState } from 'react'
import { FileVideo, CheckCircle2, XCircle, RotateCw, Sparkles, ShieldCheck } from 'lucide-react'
import { fetchClips, approveClip, rejectClip } from '../services/api'
import { GeneratedClipItem } from '../types'

export const ContentLibraryPage: React.FC = () => {
  const [clips, setClips] = useState<GeneratedClipItem[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await fetchClips()
      setClips(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const handleApprove = async (id: number) => {
    await approveClip(id)
    load()
  }

  const handleReject = async (id: number) => {
    await rejectClip(id)
    load()
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold font-brand tracking-wide text-slate-100 flex items-center space-x-2">
            <FileVideo className="w-5 h-5 text-purple-400" />
            <span>CONTENT LIBRARY & 9:16 PREVIEW</span>
          </h2>
          <p className="text-xs text-slate-400">
            Rendered 1080×1920 vertical videos with burned-in animated subtitles and Gaming Uncut branding.
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

      {clips.length === 0 ? (
        <div className="bg-[#111827] border border-gaming-border rounded-2xl p-12 text-center text-slate-500 text-xs">
          No clips generated yet. Paste a gaming video URL in 'Create Reel' to generate your first 60-second clip!
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {clips.map((clip) => {
            const isHighQuality = clip.quality_score >= 80

            return (
              <div
                key={clip.id}
                className="bg-[#111827] border border-gaming-border rounded-2xl overflow-hidden flex flex-col justify-between p-4 space-y-3 shadow-xl"
              >
                {/* 9:16 Video Player Container */}
                <div className="relative rounded-xl overflow-hidden bg-black aspect-vertical w-full max-h-96 flex items-center justify-center border border-slate-800">
                  <video
                    src={clip.video_url}
                    controls
                    preload="metadata"
                    poster={clip.thumbnail_url}
                    className="w-full h-full object-contain"
                  />
                  <div className="absolute top-2 right-2 flex items-center space-x-1.5">
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                        isHighQuality
                          ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40'
                          : 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                      }`}
                    >
                      {clip.quality_score}% Quality
                    </span>
                  </div>
                </div>

                {/* Metadata Details */}
                <div className="space-y-1.5 flex-1">
                  <span className="text-[10px] uppercase font-mono font-bold text-purple-400 bg-purple-500/10 px-2 py-0.5 rounded">
                    {clip.topic || 'Gameplay Climax'}
                  </span>
                  <h4 className="font-bold text-xs text-slate-200 line-clamp-2 leading-snug">{clip.title}</h4>
                  <p className="text-[11px] text-slate-400 line-clamp-2">{clip.description}</p>
                  <p className="text-[10px] font-mono text-cyan-400 line-clamp-1">{clip.hashtags}</p>
                </div>

                {/* Action Bar */}
                <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between">
                  <span className="text-[10px] text-slate-500 font-mono">
                    {clip.duration_sec.toFixed(1)}s • 1080×1920
                  </span>
                  <div className="flex items-center space-x-2">
                    <button
                      onClick={() => handleApprove(clip.id)}
                      className="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-xs font-semibold flex items-center space-x-1 transition"
                    >
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      <span>Approve</span>
                    </button>
                    <button
                      onClick={() => handleReject(clip.id)}
                      className="px-2.5 py-1 bg-rose-600/30 hover:bg-rose-600/50 text-rose-300 rounded-lg text-xs font-semibold flex items-center space-x-1 transition"
                    >
                      <XCircle className="w-3.5 h-3.5" />
                      <span>Reject</span>
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
