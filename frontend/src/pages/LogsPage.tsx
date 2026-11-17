import React, { useEffect, useState } from 'react'
import { Terminal, RotateCw, AlertTriangle, AlertCircle, Info, Search, ChevronDown, ChevronRight } from 'lucide-react'
import { fetchLogs } from '../services/api'
import { SystemLogItem } from '../types'

export const LogsPage: React.FC = () => {
  const [logs, setLogs] = useState<SystemLogItem[]>([])
  const [loading, setLoading] = useState(false)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [selectedLevel, setSelectedLevel] = useState<'ALL' | 'ERROR' | 'WARNING' | 'INFO'>('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [expandedLogId, setExpandedLogId] = useState<number | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      setFetchError(null)
      const data = await fetchLogs(selectedLevel === 'ALL' ? undefined : selectedLevel)
      setLogs(data)
    } catch (err: any) {
      setFetchError(err.message || 'Failed to fetch audit logs from backend.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [selectedLevel])

  const filteredLogs = logs.filter((l) => {
    if (!searchQuery.trim()) return true
    const q = searchQuery.toLowerCase()
    return (
      l.message.toLowerCase().includes(q) ||
      l.module.toLowerCase().includes(q) ||
      (l.metadata_json && l.metadata_json.toLowerCase().includes(q))
    )
  })

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold font-brand tracking-wide text-slate-100 flex items-center space-x-2">
            <Terminal className="w-5 h-5 text-purple-400" />
            <span>AUDIT LOGS & DIAGNOSTICS</span>
          </h2>
          <p className="text-xs text-slate-400">
            Real-time traces of API responses, publishing failures, rate limits, and platform errors.
          </p>
        </div>

        <div className="flex items-center space-x-2">
          {/* Level Filter */}
          <div className="bg-[#111827] border border-gaming-border p-1 rounded-xl flex items-center space-x-1 text-xs">
            {(['ALL', 'ERROR', 'WARNING', 'INFO'] as const).map((lvl) => (
              <button
                key={lvl}
                onClick={() => setSelectedLevel(lvl)}
                className={`px-2.5 py-1 rounded-lg transition font-medium ${
                  selectedLevel === lvl
                    ? 'bg-purple-600 text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
                }`}
              >
                {lvl}
              </button>
            ))}
          </div>

          <button
            onClick={load}
            disabled={loading}
            className="bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs px-3 py-2 rounded-xl flex items-center space-x-1.5 transition border border-gaming-border"
          >
            <RotateCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* API Error Notification */}
      {fetchError && (
        <div className="bg-rose-500/10 border border-rose-500/30 text-rose-300 px-4 py-3 rounded-2xl flex items-center justify-between text-xs animate-in fade-in">
          <div className="flex items-center space-x-2">
            <AlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
            <span>{fetchError}</span>
          </div>
          <button
            onClick={load}
            className="text-rose-200 underline hover:text-white font-medium ml-4 transition"
          >
            Retry Connection
          </button>
        </div>
      )}

      {/* Search Input Bar */}
      <div className="relative">
        <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search logs by platform (Facebook, Instagram, YouTube), Job ID, error code, or message..."
          className="w-full bg-[#111827] border border-gaming-border rounded-xl pl-10 pr-4 py-2 text-xs text-slate-200 placeholder-slate-500 focus:outline-none focus:border-purple-500 transition font-sans"
        />
      </div>

      {/* Logs Terminal Container */}
      <div className="bg-[#111827] border border-gaming-border rounded-2xl p-4 max-h-[640px] overflow-y-auto font-mono text-xs space-y-2 shadow-xl">
        {filteredLogs.length === 0 ? (
          <div className="p-8 text-center text-slate-500 space-y-1">
            <p>No audit logs matching your current filter.</p>
            {searchQuery && (
              <p className="text-[11px] text-slate-600">
                Try clearing your search query &quot;{searchQuery}&quot;
              </p>
            )}
          </div>
        ) : (
          filteredLogs.map((l) => {
            const isError = l.level === 'ERROR'
            const isWarning = l.level === 'WARNING'
            const isExpanded = expandedLogId === l.id

            let parsedMeta: any = null
            if (l.metadata_json) {
              try {
                parsedMeta = JSON.parse(l.metadata_json)
              } catch {
                parsedMeta = null
              }
            }

            return (
              <div
                key={l.id}
                className={`p-2 rounded-xl transition border ${
                  isError
                    ? 'bg-rose-950/20 border-rose-900/30 text-rose-300'
                    : isWarning
                    ? 'bg-amber-950/20 border-amber-900/30 text-amber-300'
                    : 'bg-slate-900/40 border-slate-800/40 text-slate-300'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-start space-x-2 leading-relaxed flex-1">
                    <span className="text-slate-500 flex-shrink-0 select-none">
                      [{new Date(l.created_at).toLocaleTimeString()}]
                    </span>
                    <span
                      className={`font-bold flex-shrink-0 px-1.5 py-0.5 rounded text-[10px] ${
                        isError
                          ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30'
                          : isWarning
                          ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                          : 'bg-purple-500/20 text-purple-400 border border-purple-500/30'
                      }`}
                    >
                      {l.level}
                    </span>
                    <span className="font-bold flex-shrink-0 text-cyan-400">
                      [{l.module.toUpperCase()}]
                    </span>
                    <span className="break-all whitespace-pre-wrap">{l.message}</span>
                  </div>

                  {parsedMeta && (
                    <button
                      onClick={() => setExpandedLogId(isExpanded ? null : l.id)}
                      className="text-slate-400 hover:text-slate-200 flex-shrink-0 p-1 rounded hover:bg-slate-800 transition"
                      title="Toggle diagnostic details"
                    >
                      {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                    </button>
                  )}
                </div>

                {/* Collapsible Structured Metadata */}
                {isExpanded && parsedMeta && (
                  <div className="mt-2.5 pt-2.5 border-t border-slate-800/60 pl-4 text-[11px] space-y-1 text-slate-400 animate-in fade-in">
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                      {parsedMeta.platform && (
                        <div>
                          <span className="text-slate-500">Platform: </span>
                          <span className="text-slate-200 font-semibold">{parsedMeta.platform}</span>
                        </div>
                      )}
                      {parsedMeta.publishing_job_id && (
                        <div>
                          <span className="text-slate-500">Job ID: </span>
                          <span className="text-purple-300">#{parsedMeta.publishing_job_id}</span>
                        </div>
                      )}
                      {parsedMeta.clip_id && (
                        <div>
                          <span className="text-slate-500">Clip ID: </span>
                          <span className="text-cyan-300">#{parsedMeta.clip_id}</span>
                        </div>
                      )}
                      {parsedMeta.stage && (
                        <div>
                          <span className="text-slate-500">Stage: </span>
                          <span className="text-amber-300">{parsedMeta.stage}</span>
                        </div>
                      )}
                      {parsedMeta.error_code && (
                        <div>
                          <span className="text-slate-500">Error Code: </span>
                          <span className="text-rose-300">{parsedMeta.error_code}</span>
                        </div>
                      )}
                      {parsedMeta.http_status && (
                        <div>
                          <span className="text-slate-500">HTTP Status: </span>
                          <span className="text-rose-300">{parsedMeta.http_status}</span>
                        </div>
                      )}
                      {parsedMeta.fbtrace_id && (
                        <div className="col-span-2">
                          <span className="text-slate-500">FB Trace ID: </span>
                          <span className="text-slate-300 select-all">{parsedMeta.fbtrace_id}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
