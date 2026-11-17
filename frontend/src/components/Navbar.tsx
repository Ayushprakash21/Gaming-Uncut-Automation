import React from 'react'
import { Gamepad2, Youtube, Instagram, Facebook, Sparkles, Activity } from 'lucide-react'
import { OverviewData } from '../types'

interface NavbarProps {
  overview: OverviewData | null
  activeMode: string
  setActiveMode: (mode: string) => void
}

export const Navbar: React.FC<NavbarProps> = ({ overview, activeMode, setActiveMode }) => {
  const ytConnected = overview?.platforms.youtube.connected ?? false
  const igConnected = overview?.platforms.instagram.connected ?? false
  const fbConnected = overview?.platforms.facebook.connected ?? false
  const geminiConnected = overview?.platforms.gemini.connected ?? false

  return (
    <header className="h-16 border-b border-gaming-border bg-[#0e1320]/90 backdrop-blur-md sticky top-0 z-40 px-6 flex items-center justify-between">
      <div className="flex items-center space-x-3">
        <div className="bg-gradient-to-tr from-purple-600 to-indigo-500 p-2 rounded-xl text-white shadow-lg shadow-purple-500/20">
          <Gamepad2 className="w-5 h-5" />
        </div>
        <div>
          <div className="flex items-center space-x-2">
            <span className="font-brand font-bold text-lg tracking-wider bg-gradient-to-r from-purple-400 via-pink-400 to-amber-300 bg-clip-text text-transparent">
              GAMING UNCUT
            </span>
            <span className="text-[10px] uppercase font-bold tracking-widest px-2 py-0.5 rounded bg-purple-500/20 text-purple-300 border border-purple-500/30">
              AUTOMATION
            </span>
          </div>
          <p className="text-[10px] text-slate-400 tracking-wider font-semibold">NO FILTER. JUST GAMEPLAY.</p>
        </div>
      </div>

      <div className="flex items-center space-x-4">
        {/* Connection Indicators */}
        <div className="hidden md:flex items-center space-x-3 bg-slate-900/80 px-3 py-1.5 rounded-lg border border-slate-800 text-xs">
          <span className="flex items-center space-x-1">
            <Youtube className={`w-3.5 h-3.5 ${ytConnected ? 'text-red-500' : 'text-slate-600'}`} />
            <span className={ytConnected ? 'text-slate-200' : 'text-slate-500'}>Shorts</span>
          </span>
          <span className="text-slate-700">•</span>
          <span className="flex items-center space-x-1">
            <Instagram className={`w-3.5 h-3.5 ${igConnected ? 'text-pink-500' : 'text-slate-600'}`} />
            <span className={igConnected ? 'text-slate-200' : 'text-slate-500'}>Instagram</span>
          </span>
          <span className="text-slate-700">•</span>
          <span className="flex items-center space-x-1">
            <Facebook className={`w-3.5 h-3.5 ${fbConnected ? 'text-blue-500' : 'text-slate-600'}`} />
            <span className={fbConnected ? 'text-slate-200' : 'text-slate-500'}>Facebook</span>
          </span>
          <span className="text-slate-700">•</span>
          <span className="flex items-center space-x-1">
            <Sparkles className={`w-3.5 h-3.5 ${geminiConnected ? 'text-amber-400' : 'text-slate-600'}`} />
            <span className={geminiConnected ? 'text-slate-200' : 'text-slate-500'}>Gemini AI</span>
          </span>
        </div>

        {/* Mode Toggle */}
        <div className="flex items-center bg-slate-900 p-0.5 rounded-lg border border-slate-800 text-xs">
          <button
            onClick={() => setActiveMode('auto')}
            className={`px-2.5 py-1 rounded font-semibold transition ${
              activeMode === 'auto'
                ? 'bg-emerald-600 text-white shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Auto Publish
          </button>
          <button
            onClick={() => setActiveMode('approval')}
            className={`px-2.5 py-1 rounded font-semibold transition ${
              activeMode === 'approval'
                ? 'bg-purple-600 text-white shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Approval Mode
          </button>
        </div>
      </div>
    </header>
  )
}
