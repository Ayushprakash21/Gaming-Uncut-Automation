import React, { useEffect, useState } from 'react'
import { RotateCw, CheckCircle2, Clock, AlertTriangle, ListOrdered, Film, RefreshCw, XCircle, Trash2 } from 'lucide-react'
import { fetchJobs, retryJob, cancelJob, dismissJob } from '../services/api'
import { ProcessingJobItem } from '../types'
import { formatDateTimeIST } from '../utils/timezone'

export const QueuePage: React.FC = () => {
  const [jobs, setJobs] = useState<ProcessingJobItem[]>([])
  const [loading, setLoading] = useState(false)
  const [retryingJobs, setRetryingJobs] = useState<Record<number, boolean>>({})
  const [cancellingJobs, setCancellingJobs] = useState<Record<number, boolean>>({})
  const [dismissingJobs, setDismissingJobs] = useState<Record<number, boolean>>({})
  const [confirmCancelJobId, setConfirmCancelJobId] = useState<number | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const data = await fetchJobs()
      setJobs(data)
    } finally {
      setLoading(false)
    }
  }

  const handleRetry = async (jobId: number) => {
    setRetryingJobs((prev) => ({ ...prev, [jobId]: true }))
    try {
      await retryJob(jobId)
      await load()
    } catch (err: any) {
      alert(`Retry failed: ${err.message}`)
    } finally {
      setRetryingJobs((prev) => ({ ...prev, [jobId]: false }))
    }
  }

  const handleCancel = async (jobId: number) => {
    setCancellingJobs((prev) => ({ ...prev, [jobId]: true }))
    try {
      await cancelJob(jobId)
      await load()
    } catch (err: any) {
      alert(`Cancellation failed: ${err.message}`)
    } finally {
      setCancellingJobs((prev) => ({ ...prev, [jobId]: false }))
    }
  }

  const handleDismiss = async (jobId: number) => {
    setDismissingJobs((prev) => ({ ...prev, [jobId]: true }))
    try {
      await dismissJob(jobId)
      await load()
    } catch (err: any) {
      alert(`Dismiss failed: ${err.message}`)
    } finally {
      setDismissingJobs((prev) => ({ ...prev, [jobId]: false }))
    }
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 3000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="space-y-6">
      {/* Confirmation Modal */}
      {confirmCancelJobId !== null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
          <div className="bg-[#111827] border border-gaming-border rounded-xl p-5 max-w-sm w-full shadow-2xl space-y-4 animate-in fade-in zoom-in-95 duration-150">
            <div className="flex items-center space-x-2.5 text-rose-400">
              <AlertTriangle className="w-5 h-5 flex-shrink-0" />
              <h3 className="font-bold text-sm text-slate-100">Cancel Video Processing</h3>
            </div>
            <p className="text-xs text-slate-300">
              Cancel this video processing job?
            </p>
            <div className="flex items-center justify-end space-x-2 pt-2">
              <button
                onClick={() => setConfirmCancelJobId(null)}
                className="px-3.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-medium transition"
              >
                Keep Processing
              </button>
              <button
                onClick={() => {
                  const id = confirmCancelJobId
                  setConfirmCancelJobId(null)
                  if (id !== null) {
                    handleCancel(id)
                  }
                }}
                className="px-3.5 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-white text-xs font-semibold transition shadow-lg shadow-rose-950"
              >
                Confirm Cancellation
              </button>
            </div>
          </div>
        </div>
      )}

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
              const isStalled = job.status === 'stalled'
              const isCancelling = job.status === 'cancelling' || cancellingJobs[job.id]
              const isCancelled = job.status === 'cancelled'
              const isProcessing = job.status === 'processing' || job.status === 'queued' || job.status === 'pending'
              const canRetry = isFailed || isStalled || isCancelled

              return (
                <div key={job.id} className="p-4 space-y-2.5 hover:bg-slate-900/40 transition">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-2.5">
                      <Film className="w-4 h-4 text-purple-400" />
                      <span className="font-semibold text-xs text-slate-200">{job.source_title}</span>
                      <span className="text-[10px] text-slate-500 font-mono">Job #{job.id}</span>
                    </div>

                    <div className="flex items-center space-x-2">
                      {job.detected_game && (
                        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded border flex items-center space-x-1 ${
                          (job.game_confidence || 0) >= 0.70
                            ? 'bg-purple-950/60 text-purple-300 border-purple-700/50'
                            : 'bg-slate-800 text-slate-400 border-slate-700'
                        }`}>
                          <span>🎮 {job.detected_game}</span>
                          {(job.game_confidence || 0) > 0 && (
                            <span className="text-purple-400 font-mono">({Math.round((job.game_confidence || 0) * 100)}%)</span>
                          )}
                        </span>
                      )}
                      {job.gameplay_event && (
                        <span className="text-[10px] font-medium px-2 py-0.5 rounded bg-cyan-950/50 text-cyan-300 border border-cyan-800/40">
                          ⚡ {job.gameplay_event}
                        </span>
                      )}
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wider ${
                          isCompleted
                            ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                            : isFailed
                            ? 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                            : isStalled
                            ? 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
                            : isCancelling
                            ? 'bg-amber-500/10 text-amber-400 border border-amber-500/30 animate-pulse'
                            : isCancelled
                            ? 'bg-rose-950/40 text-rose-400 border border-rose-800/40'
                            : 'bg-purple-500/10 text-purple-300 border border-purple-500/30 animate-pulse'
                        }`}
                      >
                        {isCancelling ? 'CANCELLING' : isCancelled ? 'CANCELLED' : job.status}
                      </span>
                    </div>
                  </div>

                  {/* Progress Bar & Current Step */}
                  {!isCancelled ? (
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
                              : isStalled
                              ? 'bg-amber-500'
                              : isCancelling
                              ? 'bg-amber-400'
                              : 'bg-gradient-to-r from-purple-500 to-cyan-400'
                          }`}
                          style={{ width: `${job.progress_pct}%` }}
                        />
                      </div>
                    </div>
                  ) : (
                    /* Cancelled State Display: Stopped/hidden progress bar, showing cancellation time */
                    <div className="flex items-center justify-between text-[11px] py-1 bg-slate-900/60 rounded px-2.5 border border-slate-800">
                      <div className="flex items-center space-x-1.5 text-rose-400">
                        <XCircle className="w-3.5 h-3.5" />
                        <span className="font-medium">Job processing cancelled by user</span>
                      </div>
                      <div className="text-[10px] text-slate-500 font-mono">
                        {job.cancelled_at || job.completed_at ? (
                          <span>
                            Cancelled: {formatDateTimeIST(job.cancelled_at || job.completed_at)}
                          </span>
                        ) : (
                          <span>Cancelled</span>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Error & Warning Messages */}
                  {job.error_message && !isCancelled && (
                    <div className="space-y-2">
                      <div className={`text-[11px] p-2 rounded-lg border font-mono ${
                        isStalled 
                          ? 'text-amber-300 bg-amber-500/10 border-amber-500/20' 
                          : 'text-rose-400 bg-rose-500/10 border-rose-500/20'
                      }`}>
                        {isStalled ? 'Warning' : 'Error'}: {job.error_message}
                      </div>
                    </div>
                  )}

                  {/* Actions Bar */}
                  <div className="flex items-center justify-end space-x-2 pt-1">
                    {/* CANCELLING state: Disabled button */}
                    {isCancelling && (
                      <button
                        disabled
                        className="bg-slate-800/60 border border-slate-700 text-slate-400 text-[11px] font-semibold px-3 py-1 rounded-md flex items-center space-x-1.5 opacity-70 cursor-not-allowed"
                      >
                        <RefreshCw className="w-3 h-3 animate-spin text-amber-400" />
                        <span>Cancelling...</span>
                      </button>
                    )}

                    {/* PROCESSING / QUEUED / PENDING state: Cancel button */}
                    {isProcessing && !isCancelling && (
                      <button
                        onClick={() => setConfirmCancelJobId(job.id)}
                        className="bg-rose-950/40 hover:bg-rose-900/60 border border-rose-800/50 text-rose-300 hover:text-rose-200 text-[11px] font-semibold px-3 py-1 rounded-md flex items-center space-x-1.5 transition"
                      >
                        <XCircle className="w-3 h-3 text-rose-400" />
                        <span>Cancel Job</span>
                      </button>
                    )}

                    {/* FAILED / STALLED state: Retry Job and Dismiss */}
                    {(isFailed || isStalled) && (
                      <>
                        <button
                          onClick={() => handleDismiss(job.id)}
                          disabled={dismissingJobs[job.id]}
                          className="bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-300 text-[11px] font-medium px-2.5 py-1 rounded-md flex items-center space-x-1 transition"
                        >
                          <Trash2 className="w-3 h-3" />
                          <span>{dismissingJobs[job.id] ? 'Dismissing...' : 'Dismiss'}</span>
                        </button>
                        <button
                          onClick={() => handleRetry(job.id)}
                          disabled={retryingJobs[job.id]}
                          className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-[11px] font-semibold px-3 py-1 rounded-md flex items-center space-x-1.5 transition shadow"
                        >
                          <RefreshCw className={`w-3 h-3 ${retryingJobs[job.id] ? 'animate-spin' : ''}`} />
                          <span>{retryingJobs[job.id] ? 'Retrying...' : 'Retry Job'}</span>
                        </button>
                      </>
                    )}

                    {/* CANCELLED state: Retry Job and Dismiss */}
                    {isCancelled && (
                      <>
                        <button
                          onClick={() => handleDismiss(job.id)}
                          disabled={dismissingJobs[job.id]}
                          className="bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-300 text-[11px] font-medium px-2.5 py-1 rounded-md flex items-center space-x-1 transition"
                        >
                          <Trash2 className="w-3 h-3" />
                          <span>{dismissingJobs[job.id] ? 'Dismissing...' : 'Dismiss'}</span>
                        </button>
                        <button
                          onClick={() => handleRetry(job.id)}
                          disabled={retryingJobs[job.id]}
                          className="bg-purple-600 hover:bg-purple-500 disabled:opacity-50 text-white text-[11px] font-semibold px-3 py-1 rounded-md flex items-center space-x-1.5 transition shadow"
                        >
                          <RefreshCw className={`w-3 h-3 ${retryingJobs[job.id] ? 'animate-spin' : ''}`} />
                          <span>{retryingJobs[job.id] ? 'Retrying...' : 'Retry Job'}</span>
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}