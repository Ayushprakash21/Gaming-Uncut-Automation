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
  Terminal
} from 'lucide-react'

interface SidebarProps {
  currentTab: string
  setCurrentTab: (tab: string) => void
  dailyPublished: number
  dailyTarget: number
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentTab,
  setCurrentTab,
  dailyPublished,
  dailyTarget
}) => {
  const navItems = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'create', label: 'Create Reel', icon: Film, highlight: true },
    { id: 'queue', label: 'Processing Queue', icon: ListOrdered },
    { id: 'published', label: 'Published Videos', icon: CheckCircle2 },
    { id: 'library', label: 'Content Library', icon: FileVideo },
    { id: 'scheduler', label: 'Scheduler', icon: CalendarDays },
    { id: 'analytics', label: 'Analytics', icon: BarChart3 },
    { id: 'connections', label: 'Platform Connections', icon: Share2 },
    { id: 'ai-settings', label: 'AI Settings', icon: Sparkles },
    { id: 'logs', label: 'Logs & Diagnostics', icon: Terminal },
  ]

  const pct = Math.min(100, Math.round((dailyPublished / (dailyTarget || 12)) * 100))

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
      <div className="bg-[#121826] border border-gaming-border rounded-xl p-3.5 space-y-2 mt-auto">
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-400 font-medium">Daily Target</span>
          <span className="font-bold text-emerald-400 font-mono">
            {dailyPublished} / {dailyTarget}
          </span>
        </div>
        <div className="w-full bg-slate-800 h-2 rounded-full overflow-hidden">
          <div
            className="bg-gradient-to-r from-purple-500 to-emerald-400 h-full transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-[10px] text-slate-500 leading-tight">
          Targeting 10–15 high-retention clips/day with automated quota distribution.
        </p>
      </div>
    </aside>
  )
}
