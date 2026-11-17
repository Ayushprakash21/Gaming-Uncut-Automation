import React from 'react'
import {
  Film,
  CheckCircle2,
  XCircle,
  Clock,
  Youtube,
  Instagram,
  Facebook,
  Sparkles,
  TrendingUp,
  Cpu
} from 'lucide-react'
import { OverviewData } from '../types'

interface OverviewPageProps {
  data: OverviewData | null
  onNavigate: (tab: string) => void
}

export const OverviewPage: React.FC<OverviewPageProps> = ({ data, onNavigate }) => {
  const kpis = data?.kpis || {
    total_videos_processed: 0,
    total_clips_generated: 0,
    published_count: 0,
    failed_count: 0,
    pending_queue: 0,
    today_published: 0,
    daily_target: 12,
    target_progress_pct: 0
  }

  const platforms = data?.platforms || {
    youtube: { connected: false, quota_used_today: 0 },
    instagram: { connected: false },
    facebook: { connected: false },
    gemini: { connected: false }
  }

  return (
    <div className="space-y-6">
      {/* Hero Welcome */}
      <div className="bg-gradient-to-r from-purple-950/40 via-slate-900 to-indigo-950/30 border border-purple-500/20 rounded-2xl p-6 flex items-center justify-between">
        <div className="space-y-1">
          <div className="flex items-center space-x-2">
            <span className="font-brand text-2xl font-bold tracking-wide text-white">GAMING UNCUT</span>
            <span className="text-[10px] tracking-widest uppercase bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded font-mono font-bold">
              SYSTEM ONLINE
            </span>
          </div>
          <p className="text-xs text-slate-300 font-medium">NO FILTER. JUST GAMEPLAY.</p>
          <p className="text-xs text-slate-400 max-w-xl">
            Autonomous 60-second vertical video production powered by NVIDIA NVENC hardware acceleration and Gemini multimodal gameplay understanding.
          </p>
        </div>

        <button
          onClick={() => onNavigate('create')}
          className="bg-purple-600 hover:bg-purple-500 text-white font-bold text-xs px-5 py-3 rounded-xl shadow-lg shadow-purple-600/30 transition flex items-center space-x-2"
        >
          <Film className="w-4 h-4" />
          <span>PASTE GAMING URL</span>
        </button>
      </div>

      {/* KPI Metric Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-1 shadow">
          <span className="text-[11px] text-slate-400 font-medium flex items-center justify-between">
            <span>Processed Videos</span>
            <Film className="w-4 h-4 text-purple-400" />
          </span>
          <p className="text-2xl font-bold font-mono text-slate-100">{kpis.total_videos_processed}</p>
          <span className="text-[10px] text-slate-500">Source games analyzed</span>
        </div>

        <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-1 shadow">
          <span className="text-[11px] text-slate-400 font-medium flex items-center justify-between">
            <span>Clips Generated</span>
            <Sparkles className="w-4 h-4 text-cyan-400" />
          </span>
          <p className="text-2xl font-bold font-mono text-slate-100">{kpis.total_clips_generated}</p>
          <span className="text-[10px] text-slate-500">60s 1080×1920 renders</span>
        </div>

        <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-1 shadow">
          <span className="text-[11px] text-slate-400 font-medium flex items-center justify-between">
            <span>Published Today</span>
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          </span>
          <p className="text-2xl font-bold font-mono text-emerald-400">
            {kpis.today_published} <span className="text-xs text-slate-500 font-sans">/ {kpis.daily_target}</span>
          </p>
          <span className="text-[10px] text-slate-500">{kpis.target_progress_pct}% of daily target</span>
        </div>

        <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-1 shadow">
          <span className="text-[11px] text-slate-400 font-medium flex items-center justify-between">
            <span>Pending In Queue</span>
            <Clock className="w-4 h-4 text-amber-400" />
          </span>
          <p className="text-2xl font-bold font-mono text-slate-100">{kpis.pending_queue}</p>
          <span className="text-[10px] text-slate-500">Scheduled for dispatch</span>
        </div>
      </div>

      {/* Platform Connection Health */}
      <div className="space-y-3">
        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">Official Platform Integrations</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* YouTube Shorts */}
          <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Youtube className="w-5 h-5 text-red-500" />
                <span className="font-bold text-xs text-slate-200">YouTube Shorts</span>
              </div>
              <span
                className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                  platforms.youtube.connected
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                    : 'bg-slate-800 text-slate-500'
                }`}
              >
                {platforms.youtube.connected ? 'AUTHENTICATED' : 'NOT CONNECTED'}
              </span>
            </div>
            <p className="text-[11px] text-slate-400">
              Desktop OAuth client ready. Videos published with #Shorts categorization.
            </p>
            <div className="text-[10px] font-mono text-slate-500">
              Quota consumed today: {platforms.youtube.quota_used_today.toLocaleString()} / 10,000 units
            </div>
          </div>

          {/* Instagram Reels */}
          <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Instagram className="w-5 h-5 text-pink-500" />
                <span className="font-bold text-xs text-slate-200">Instagram Reels</span>
              </div>
              <span
                className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                  platforms.instagram.connected
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                    : 'bg-slate-800 text-slate-500'
                }`}
              >
                {platforms.instagram.connected ? 'AUTHENTICATED' : 'NOT CONNECTED'}
              </span>
            </div>
            <p className="text-[11px] text-slate-400">
              Direct resumable chunk upload via rupload.facebook.com protocol.
            </p>
            <div className="text-[10px] font-mono text-slate-500">
              Daily Limit: 50 Reels / rolling 24-hr window
            </div>
          </div>

          {/* Facebook Reels */}
          <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Facebook className="w-5 h-5 text-blue-500" />
                <span className="font-bold text-xs text-slate-200">Facebook Reels</span>
              </div>
              <span
                className={`text-[10px] font-bold px-2 py-0.5 rounded ${
                  platforms.facebook.connected
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                    : 'bg-slate-800 text-slate-500'
                }`}
              >
                {platforms.facebook.connected ? 'AUTHENTICATED' : 'NOT CONNECTED'}
              </span>
            </div>
            <p className="text-[11px] text-slate-400">
              Official Page Video Reels session init, binary push, and instant verification.
            </p>
            <div className="text-[10px] font-mono text-slate-500">
              Direct Page Access Token integration
            </div>
          </div>
        </div>
      </div>

      {/* Hardware & Encoding Telemetry */}
      <div className="bg-[#111827] border border-gaming-border rounded-xl p-4 flex items-center justify-between text-xs">
        <div className="flex items-center space-x-3">
          <div className="p-2 rounded-lg bg-purple-500/10 text-purple-400">
            <Cpu className="w-5 h-5" />
          </div>
          <div>
            <span className="font-bold text-slate-200">Hardware Accelerator: NVIDIA GeForce GTX 1650</span>
            <p className="text-[11px] text-slate-400">H.264 NVENC hardware encoder active • Sub-minute 1080×1920 60FPS vertical rendering</p>
          </div>
        </div>
        <span className="text-[11px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 px-2.5 py-1 rounded font-semibold font-mono">
          CUDA ACCELERATED
        </span>
      </div>
    </div>
  )
}
