import React, { useState } from 'react'
import { Sparkles, Youtube, Instagram, Facebook, Wand2, ShieldCheck, CheckCircle2, Film } from 'lucide-react'
import { createReel } from '../services/api'

interface CreateReelPageProps {
  onReelCreated: () => void
  activeMode: string
}

export const CreateReelPage: React.FC<CreateReelPageProps> = ({ onReelCreated, activeMode }) => {
  const [url, setUrl] = useState('')
  const [template, setTemplate] = useState('blurred_bg')
  const [platforms, setPlatforms] = useState<string[]>(['youtube', 'instagram', 'facebook'])
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [successMsg, setSuccessMsg] = useState('')

  const togglePlatform = (p: string) => {
    setPlatforms((prev) =>
      prev.includes(p) ? prev.filter((item) => item !== p) : [...prev, p]
    )
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!url.trim()) return

    setSubmitting(true)
    setSuccessMsg('')
    try {
      await createReel({
        url: url.trim(),
        template,
        publish_mode: activeMode,
        platforms,
        notes
      })
      setSuccessMsg('Gaming footage accepted! AI highlight detection and 60-second vertical rendering started.')
      setUrl('')
      setTimeout(() => onReelCreated(), 1500)
    } catch (err: any) {
      alert('Error launching pipeline: ' + err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h2 className="text-xl font-bold font-brand tracking-wide text-slate-100 flex items-center space-x-2">
          <span>CREATE REEL & AUTONOMOUS DISPATCH</span>
          <span className="text-xs bg-purple-500/20 text-purple-300 px-2 py-0.5 rounded font-sans font-semibold">
            NO FILTER. JUST GAMEPLAY.
          </span>
        </h2>
        <p className="text-xs text-slate-400 mt-1">
          Paste an authorized YouTube URL, direct MP4/MKV stream, or local recording path. The AI analyzes peak moments, cuts a 60s vertical video, applies animated subtitles, and publishes.
        </p>
      </div>

      {successMsg && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs px-4 py-3 rounded-xl flex items-center space-x-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          <span>{successMsg}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} className="bg-[#111827] border border-gaming-border rounded-2xl p-6 space-y-6 shadow-xl">
        {/* URL Input Box */}
        <div className="space-y-2">
          <label className="block text-xs font-bold uppercase tracking-wider text-purple-400">
            Paste Your Gaming Video URL or Local Path
          </label>
          <div className="relative">
            <input
              type="text"
              required
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=... or https://.../video.mp4 or D:\Recordings\game.mp4"
              className="w-full bg-[#0a0d14] border border-gaming-border focus:border-purple-500 focus:ring-1 focus:ring-purple-500 rounded-xl px-4 py-3.5 text-xs text-slate-100 placeholder-slate-500 transition font-mono"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[10px] text-slate-400">
            <span className="text-slate-500">Supported Sources:</span>
            <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300">Creator Authorized YouTube</span>
            <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300">Direct HTTP/HTTPS MP4</span>
            <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-300">Local OBS / Shadowplay MP4/MKV</span>
          </div>
        </div>

        {/* Vertical Layout Templates */}
        <div className="space-y-3">
          <label className="block text-xs font-bold uppercase tracking-wider text-slate-300">
            Select 1080×1920 Vertical Template
          </label>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[
              {
                id: 'blurred_bg',
                name: 'Template B — Blurred Background',
                desc: 'Main gameplay in foreground with enlarged, smoothed Gaussian background blur.',
                badge: 'Recommended'
              },
              {
                id: 'dynamic_gameplay',
                name: 'Template A — Dynamic Gameplay',
                desc: 'Full-bleed 9:16 vertical crop centered dynamically on the core crosshair/action.',
                badge: 'Action Focus'
              },
              {
                id: 'split_layout',
                name: 'Template C — Smart Split Layout',
                desc: 'Top webcam / reaction view combined with lower main gameplay screen.',
                badge: 'Webcam Ready'
              }
            ].map((t) => (
              <div
                key={t.id}
                onClick={() => setTemplate(t.id)}
                className={`p-4 rounded-xl border cursor-pointer transition flex flex-col justify-between space-y-2 ${
                  template === t.id
                    ? 'bg-purple-600/10 border-purple-500 ring-1 ring-purple-500/40 text-purple-200'
                    : 'bg-[#0a0d14] border-gaming-border hover:border-slate-700 text-slate-400'
                }`}
              >
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-semibold text-xs text-slate-200">{t.name}</span>
                    <span className="text-[10px] bg-slate-800 text-purple-300 px-1.5 py-0.5 rounded font-mono">
                      {t.badge}
                    </span>
                  </div>
                  <p className="text-[11px] text-slate-400 leading-relaxed">{t.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Platform Targets */}
        <div className="space-y-3">
          <label className="block text-xs font-bold uppercase tracking-wider text-slate-300">
            Publishing Platforms
          </label>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[
              { id: 'youtube', name: 'YouTube Shorts', icon: Youtube, color: 'text-red-500', desc: 'Resumable API Upload (1,600 units)' },
              { id: 'instagram', name: 'Instagram Reels', icon: Instagram, color: 'text-pink-500', desc: 'Direct Binary Ruploader' },
              { id: 'facebook', name: 'Facebook Reels', icon: Facebook, color: 'text-blue-500', desc: 'Page Video Reels Session' },
            ].map((p) => {
              const Icon = p.icon
              const isChecked = platforms.includes(p.id)
              return (
                <div
                  key={p.id}
                  onClick={() => togglePlatform(p.id)}
                  className={`p-3.5 rounded-xl border cursor-pointer flex items-center space-x-3 transition ${
                    isChecked
                      ? 'bg-slate-900 border-purple-500/50'
                      : 'bg-[#0a0d14] border-gaming-border opacity-60'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={() => {}}
                    className="rounded bg-slate-800 border-slate-700 text-purple-600 focus:ring-0"
                  />
                  <Icon className={`w-5 h-5 ${p.color}`} />
                  <div>
                    <span className="block text-xs font-semibold text-slate-200">{p.name}</span>
                    <span className="block text-[10px] text-slate-500">{p.desc}</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Attribution & Authorization Notes */}
        <div className="space-y-2">
          <label className="block text-xs font-bold uppercase tracking-wider text-slate-400">
            Source Authorization & Creator Notes (Optional)
          </label>
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="e.g. My Twitch live stream recording / Authorized tournament highlights"
            className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl px-3 py-2 text-xs text-slate-300 placeholder-slate-600"
          />
        </div>

        {/* Submit Button */}
        <div className="pt-2 flex items-center justify-between border-t border-gaming-border">
          <div className="flex items-center space-x-2 text-xs text-slate-400">
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            <span>Mode: {activeMode === 'auto' ? 'Autonomous Auto-Publish' : 'Human Approval Required'}</span>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white font-bold text-xs px-6 py-3 rounded-xl shadow-lg shadow-purple-600/30 flex items-center space-x-2 transition disabled:opacity-50"
          >
            <Wand2 className="w-4 h-4" />
            <span>{submitting ? 'INITIALIZING AI...' : 'GENERATE & PUBLISH REEL'}</span>
          </button>
        </div>
      </form>
    </div>
  )
}
