import React, { useEffect, useState } from 'react'
import { RotateCw, CheckCircle2, Clock, AlertTriangle, ListOrdered, Film } from 'lucide-react'
import { fetchJobs } from '../services/api'
import { ProcessingJobItem } from '../types'

export const QueuePage: React.FC = () => {
  const [jobs, setJobs] = useState<ProcessingJobItem[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await fetchJobs()
      setJobs(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 3000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold font-brand tracking-wide text-slate-100 flex items-center space-x-2">
            <ListOrdered className="w-5 h-5 text-purple-400" />
            <span>PROCESSING QUEUE</span>
          </h2>
          <p className="text-xs text-slate-400">
            Real-time pipeline tracking: Ingestion &rarr; Audio Signal Analysis &rarr; AI Highlight Scoring &rarr; NVENC Rendering.
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
        {jobs.length === 0 ? (
          <div className="p-12 text-center text-slate-500 text-xs">
            No active processing jobs. Paste a gaming video URL to start automatic Reel production.
          </div>
        ) : (
          <div className="divide-y divide-gaming-border">
            {jobs.map((job) => {
              const isCompleted = job.status === 'completed'
              const isFailed = job.status === 'failed'
              const isRunning = job.status === 'analyzing' || job.status === 'rendering'

              return (
                <div key={job.id} className="p-4 space-y-2.5 hover:bg-slate-900/40 transition">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-2.5">
                      <Film className="w-4 h-4 text-purple-400" />
                      <span className="font-semibold text-xs text-slate-200">{job.source_title}</span>
                      <span className="text-[10px] text-slate-500 font-mono">Job #{job.id}</span>
                    </div>

                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider ${
                        isCompleted
                          ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                          : isFailed
                          ? 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                          : 'bg-purple-500/10 text-purple-300 border border-purple-500/30 animate-pulse'
                      }`}
                    >
                      {job.status}
                    </span>
                  </div>

                  {/* Progress Bar & Current Step */}
                  <div className="space-y-1">
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-slate-400">{job.current_step}</span>
                      <span className="font-mono font-bold text-purple-400">{job.progress_pct}%</span>
                    </div>
                    <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                      <div
                        className={`h-full transition-all duration-300 ${
                          isCompleted
                            ? 'bg-emerald-400'
                            : isFailed
                            ? 'bg-rose-500'
                            : 'bg-gradient-to-r from-purple-500 to-cyan-400'
                        }`}
                        style={{ width: `${job.progress_pct}%` }}
                      />
                    </div>
                  </div>

                  {job.error_message && (
                    <div className="text-[11px] text-rose-400 bg-rose-500/10 p-2 rounded-lg border border-rose-500/20 font-mono">
                      Error: {job.error_message}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
