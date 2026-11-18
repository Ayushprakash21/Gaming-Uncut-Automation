import React from 'react'
import {
  LayoutDashboard,
  Film,
  ListOrdered,
  CheckCircle2,
  FileVideo,
  CalendarDays,
  BarChart3,
  Sparkles,
  Share2,
  Sliders,
  Terminal,
  Compass
} from 'lucide-react'

interface SidebarProps {
  currentTab: string
  setCurrentTab: (tab: string) => void
  dailyGenerated: number
  targetGeneration: number
  dailyPublished: number
  targetPublishing: number
  queuedCount: number
  failedCount: number
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentTab,
  setCurrentTab,
  dailyGenerated,
  targetGeneration,
  dailyPublished,
  targetPublishing,
  queuedCount,
  failedCount
}) => {
  const navItems = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'create', label: 'Create Reel', icon: Film },
    { id: 'discovery', label: 'Video Discovery', icon: Compass, highlight: true },
    { id: 'queue', label: 'Processing Queue', icon: ListOrdered },
    { id: 'published', label: 'Published Videos', icon: CheckCircle2 },
    { id: 'library', label: 'Content Library', icon: FileVideo },
    { id: 'scheduler', label: 'Scheduler', icon: CalendarDays },
    { id: 'analytics', label: 'Analytics', icon: BarChart3 },
    { id: 'connections', label: 'Platform Connections', icon: Share2 },
    { id: 'ai-settings', label: 'AI Settings', icon: Sparkles },
    { id: 'logs', label: 'Logs & Diagnostics', icon: Terminal },
  ]

  const pctGen = Math.min(100, Math.round((dailyGenerated / (targetGeneration || 15)) * 100))
  const pctPub = Math.min(100, Math.round((dailyPublished / (targetPublishing || 15)) * 100))

  return (
    <aside className="w-64 border-r border-gaming-border bg-[#0d111c] flex flex-col p-4 space-y-1 select-none">
      <div className="flex-1 space-y-1">
        {navItems.map((item) => {
          const Icon = item.icon
          const isActive = currentTab === item.id
          return (
            <button
              key={item.id}
              onClick={() => setCurrentTab(item.id)}
              className={`w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl font-medium text-xs transition duration-150 ${
                isActive
                  ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30 shadow-sm'
                  : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
              } ${item.highlight && !isActive ? 'text-purple-400' : ''}`}
            >
              <Icon className={`w-4 h-4 ${isActive ? 'text-purple-400' : item.highlight ? 'text-purple-400' : 'text-slate-500'}`} />
              <span>{item.label}</span>
              {item.highlight && (
                <span className="ml-auto bg-purple-500/20 text-purple-300 text-[10px] px-1.5 py-0.5 rounded font-bold">
                  NEW
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Target Progress Widget */}
      <div className="bg-[#121826] border border-gaming-border rounded-xl p-3.5 space-y-3 mt-auto">
        {/* Local Generation Goal */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400 font-medium flex items-center gap-1.5">
              <Sparkles className="w-3 h-3 text-purple-400" />
              Generated
            </span>
            <span className="font-bold text-purple-400 font-mono">
              {dailyGenerated} / {targetGeneration || 15}
            </span>
          </div>
          <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
            <div
              className="bg-purple-500 h-full transition-all duration-500"
              style={{ width: `${pctGen}%` }}
            />
          </div>
        </div>

        {/* Publishing Target */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400 font-medium flex items-center gap-1.5">
              <CheckCircle2 className="w-3 h-3 text-emerald-400" />
              Published
            </span>
            <span className="font-bold text-emerald-400 font-mono">
              {dailyPublished} / {targetPublishing || 15}
            </span>
          </div>
          <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
            <div
              className="bg-gradient-to-r from-purple-500 to-emerald-400 h-full transition-all duration-500"
              style={{ width: `${pctPub}%` }}
            />
          </div>
        </div>

        {/* Queued / Failed Breakdown */}
        <div className="grid grid-cols-2 gap-2 pt-1 border-t border-slate-800/80 text-[11px]">
          <div className="flex items-center justify-between bg-slate-900/60 px-2 py-1 rounded-lg">
            <span className="text-slate-400">Queued:</span>
            <span className="font-bold text-slate-200 font-mono">{queuedCount}</span>
          </div>
          <div className="flex items-center justify-between bg-slate-900/60 px-2 py-1 rounded-lg">
            <span className="text-slate-400">Failed:</span>
            <span className="font-bold text-slate-200 font-mono">{failedCount}</span>
          </div>
        </div>

        <p className="text-[10px] text-slate-500 leading-tight">
          Configurable limits (15 clips/day default). Independent generation & API quotas.
        </p>
      </div>
    </aside>
  )
}
