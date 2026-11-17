import React, { useEffect, useState } from 'react'
import { Terminal, RotateCw } from 'lucide-react'
import { fetchLogs } from '../services/api'
import { SystemLogItem } from '../types'

export const LogsPage: React.FC = () => {
  const [logs, setLogs] = useState<SystemLogItem[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await fetchLogs()
      setLogs(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold font-brand tracking-wide text-slate-100 flex items-center space-x-2">
            <Terminal className="w-5 h-5 text-purple-400" />
            <span>AUDIT LOGS & DIAGNOSTICS</span>
          </h2>
          <p className="text-xs text-slate-400">
            Real-time traces of API responses, error codes, and encoding milestones.
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

      <div className="bg-[#111827] border border-gaming-border rounded-2xl p-4 max-h-[600px] overflow-y-auto font-mono text-xs space-y-1.5 shadow-xl">
        {logs.length === 0 ? (
          <div className="p-6 text-center text-slate-500">No logs recorded yet.</div>
        ) : (
          logs.map((l) => {
            const isError = l.level === 'ERROR'
            const isWarning = l.level === 'WARNING'

            return (
              <div
                key={l.id}
                className={`flex items-start space-x-2 py-0.5 leading-relaxed ${
                  isError ? 'text-rose-400' : isWarning ? 'text-amber-400' : 'text-slate-300'
                }`}
              >
                <span className="text-slate-600 flex-shrink-0">
                  [{new Date(l.created_at).toLocaleTimeString()}]
                </span>
                <span className="font-bold flex-shrink-0 text-purple-400">
                  [{l.module.toUpperCase()}]
                </span>
                <span className="break-all">{l.message}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
