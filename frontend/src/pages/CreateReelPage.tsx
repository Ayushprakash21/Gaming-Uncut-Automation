import React, { useState, useEffect, useRef } from 'react'
import {
  Sparkles, Youtube, Instagram, Facebook, Wand2, ShieldCheck,
  CheckCircle2, AlertCircle, FileVideo, Download, RotateCcw,
  Check, Clock, Scissors, Sliders, Play, Edit3, Save, ExternalLink
} from 'lucide-react'
import {
  validateSource, generateReel, fetchJobProgress,
  updateClipMetadata, addClipToQueue, fetchConnections
} from '../services/api'

interface CreateReelPageProps {
  onReelCreated: () => void
  activeMode: string
}

export const CreateReelPage: React.FC<CreateReelPageProps> = ({ onReelCreated, activeMode }) => {
  // Input states
  const [url, setUrl] = useState('')
  const [sourceAuthorized, setSourceAuthorized] = useState(true)
  const [targetDuration, setTargetDuration] = useState(60)
  const [template, setTemplate] = useState('blurred_bg')
  const [platforms, setPlatforms] = useState<string[]>(['youtube', 'instagram', 'facebook'])
  const [notes, setNotes] = useState('')

  // 8 Processing toggles
  const [aiHighlightDetection, setAiHighlightDetection] = useState(true)
  const [smartCropping, setSmartCropping] = useState(true)
  const [animatedCaptions, setAnimatedCaptions] = useState(true)
  const [watermark, setWatermark] = useState(true)
  const [aiTitle, setAiTitle] = useState(true)
  const [aiTopic, setAiTopic] = useState(true)
  const [aiDesc, setAiDesc] = useState(true)
  const [aiHashtags, setAiHashtags] = useState(true)

  // Validation states
  const [validating, setValidating] = useState(false)
  const [validationResult, setValidationResult] = useState<any>(null)
  const [validationError, setValidationError] = useState('')

  // Job progress states
  const [submitting, setSubmitting] = useState(false)
  const [jobId, setJobId] = useState<number | null>(null)
  const [jobProgress, setJobProgress] = useState<any>(null)
  const [completedClip, setCompletedClip] = useState<any>(null)
  const pollTimerRef = useRef<any>(null)

  // Connections status
  const [connections, setConnections] = useState<any>(null)

  // Metadata editor states
  const [isEditingMeta, setIsEditingMeta] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editTopic, setEditTopic] = useState('')
  const [editHashtags, setEditHashtags] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [saveSuccessMsg, setSaveSuccessMsg] = useState('')
  const [queueSuccessMsg, setQueueSuccessMsg] = useState('')

  useEffect(() => {
    fetchConnections().then(setConnections).catch(() => {})
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    }
  }, [])

  const togglePlatform = (p: string) => {
    setPlatforms((prev) =>
      prev.includes(p) ? prev.filter((item) => item !== p) : [...prev, p]
    )
  }

  const handlePasteClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText()
      if (text) {
        setUrl(text.trim())
        setValidationResult(null)
        setValidationError('')
      }
    } catch {
      const manual = prompt('Enter your video URL or file path:', url)
      if (manual) {
        setUrl(manual.trim())
        setValidationResult(null)
        setValidationError('')
      }
    }
  }

  const handleLocalFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0]
      setUrl(file.name)
      setValidationResult(null)
      setValidationError('')
    }
  }

  const handleValidateSource = async () => {
    if (!url.trim()) {
      setValidationError('Please enter a video URL or local file path.')
      return
    }

    setValidating(true)
    setValidationError('')
    setValidationResult(null)

    try {
      const data = await validateSource(url.trim())
      setValidationResult(data)
    } catch (err: any) {
      setValidationError(err.message || 'Source validation failed.')
    } finally {
      setValidating(false)
    }
  }

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!url.trim()) return

    if (!sourceAuthorized) {
      alert('Usage rights or creator authorization confirmation is required.')
      return
    }

    setSubmitting(true)
    setCompletedClip(null)
    setJobProgress(null)
    setQueueSuccessMsg('')

    try {
      const res = await generateReel({
        url: url.trim(),
        source_authorized: true,
        target_duration: targetDuration,
        resolution: '1080x1920',
        aspect_ratio: '9:16',
        template,
        ai_highlight_detection: aiHighlightDetection,
        smart_cropping: smartCropping,
        animated_captions: animatedCaptions,
        watermark,
        ai_title_generation: aiTitle,
        ai_topic_classification: aiTopic,
        ai_description_generation: aiDesc,
        ai_hashtag_generation: aiHashtags,
        publish_mode: activeMode,
        platforms,
        notes
      })

      const id = res.job_id
      setJobId(id)

      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
      pollTimerRef.current = setInterval(() => pollProgress(id), 1200)
    } catch (err: any) {
      alert('Error launching pipeline: ' + err.message)
      setSubmitting(false)
    }
  }

  const pollProgress = async (id: number) => {
    try {
      const job = await fetchJobProgress(id)
      setJobProgress(job)

      if (job.status === 'completed') {
        clearInterval(pollTimerRef.current)
        setSubmitting(false)
        if (job.clip) {
          setCompletedClip(job.clip)
          setEditTitle(job.clip.title || '')
          setEditTopic(job.clip.topic || 'Gaming')
          setEditHashtags(job.clip.hashtags || '')
          setEditDesc(job.clip.description || '')
        }
      } else if (job.status === 'failed') {
        clearInterval(pollTimerRef.current)
        setSubmitting(false)
      }
    } catch (err) {
      console.error('Error polling job:', err)
    }
  }

  const handleSaveMetadata = async () => {
    if (!completedClip?.id) return
    try {
      await updateClipMetadata(completedClip.id, {
        title: editTitle,
        topic: editTopic,
        hashtags: editHashtags,
        description: editDesc
      })
      setSaveSuccessMsg('Metadata saved successfully!')
      setIsEditingMeta(false)
      setTimeout(() => setSaveSuccessMsg(''), 3000)
    } catch (err: any) {
      alert('Failed to save metadata: ' + err.message)
    }
  }

  const handleAddToQueue = async () => {
    if (!completedClip?.id) return
    try {
      await addClipToQueue(completedClip.id, { platforms })
      setQueueSuccessMsg('Reel successfully added to Publishing Queue (Manual Review default)!')
      setTimeout(() => onReelCreated(), 2000)
    } catch (err: any) {
      alert('Failed to add to queue: ' + err.message)
    }
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-12">
      {/* Header */}
      <div className="flex items-center justify-between pb-3 border-b border-gaming-border">
        <div>
          <div className="flex items-center space-x-3">
            <h2 className="text-2xl font-black font-brand tracking-wide text-slate-100">
              Create Your Gaming Reel
            </h2>
            <span className="text-[11px] bg-purple-500/20 text-purple-300 border border-purple-500/30 px-2.5 py-0.5 rounded-full font-bold uppercase tracking-wider">
              NO FILTER. JUST GAMEPLAY.
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Turn gaming footage into engaging 60-second vertical videos automatically. AI detects climax moments, frames dynamic vertical crops, generates animated subtitles, and coordinates publishing.
          </p>
        </div>
      </div>

      {/* Primary Ingestion Form */}
      <form onSubmit={handleGenerate} className="bg-[#111827] border border-gaming-border rounded-2xl p-6 space-y-5 shadow-2xl">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <label className="block text-xs font-bold uppercase tracking-wider text-purple-400">
              Paste Gaming Video URL or Local Path
            </label>
            {url.includes('youtube.com') || url.includes('youtu.be') ? (
              <span className="text-[11px] px-2.5 py-0.5 rounded-full font-semibold bg-red-500/10 text-red-400 border border-red-500/30 flex items-center space-x-1">
                <Youtube className="w-3.5 h-3.5" />
                <span>YouTube Detected</span>
              </span>
            ) : url.startsWith('http') ? (
              <span className="text-[11px] px-2.5 py-0.5 rounded-full font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">
                Direct Stream
              </span>
            ) : url.length > 2 ? (
              <span className="text-[11px] px-2.5 py-0.5 rounded-full font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                Local Footage
              </span>
            ) : null}
          </div>

          <div className="flex flex-col sm:flex-row gap-2">
            <input
              type="text"
              required
              value={url}
              onChange={(e) => {
                setUrl(e.target.value)
                setValidationResult(null)
                setValidationError('')
              }}
              placeholder="https://www.youtube.com/watch?v=... or direct MP4 URL or D:\Recordings\game.mp4"
              className="flex-1 bg-[#0a0d14] border border-gaming-border focus:border-purple-500 focus:ring-1 focus:ring-purple-500 rounded-xl px-4 py-3 text-xs text-slate-100 placeholder-slate-500 font-mono transition"
            />
            <button
              type="button"
              onClick={handlePasteClipboard}
              className="px-4 py-3 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-xl text-xs font-semibold transition"
            >
              Paste URL
            </button>
            <button
              type="button"
              disabled={validating || !url.trim()}
              onClick={handleValidateSource}
              className="px-4 py-3 bg-indigo-600/30 hover:bg-indigo-600/50 text-indigo-300 border border-indigo-500/40 rounded-xl text-xs font-bold transition disabled:opacity-50"
            >
              {validating ? 'Validating...' : 'Validate Source'}
            </button>
            <button
              type="submit"
              disabled={submitting || !url.trim()}
              className="px-6 py-3 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white font-extrabold text-xs rounded-xl shadow-lg shadow-purple-600/30 flex items-center space-x-2 transition disabled:opacity-50"
            >
              <Wand2 className="w-4 h-4" />
              <span>{submitting ? 'GENERATING...' : 'GENERATE REEL'}</span>
            </button>
          </div>

          {/* Validation Feedback Banner */}
          {validationResult && (
            <div className="p-3.5 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-xs flex items-start justify-between">
              <div className="space-y-1">
                <div className="flex items-center space-x-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                  <span className="font-bold text-emerald-300 uppercase">{validationResult.platform_name}</span>
                  <span className="text-slate-400 font-mono text-[11px]">{validationResult.duration_formatted}</span>
                  <span className="text-slate-400 font-mono text-[11px]">{validationResult.resolution}</span>
                </div>
                <h4 className="font-bold text-slate-100 text-sm">{validationResult.title}</h4>
                <p className="text-slate-400 text-[11px]">Author: <span className="text-slate-200">{validationResult.author}</span></p>
                <p className="text-emerald-400 text-[11px]">{validationResult.message}</p>
              </div>
              {validationResult.thumbnail_url && (
                <img src={validationResult.thumbnail_url} alt="Thumbnail" className="w-24 h-14 object-cover rounded-lg border border-slate-700" />
              )}
            </div>
          )}

          {validationError && (
            <div className="p-3 rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-300 text-xs flex items-center space-x-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{validationError}</span>
            </div>
          )}

          {/* Alternative Local Upload & Usage Confirmation */}
          <div className="pt-2 border-t border-gaming-border flex flex-col sm:flex-row items-center justify-between text-xs text-slate-400 gap-3">
            <div className="flex items-center space-x-2">
              <span>Local video:</span>
              <label className="cursor-pointer px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg font-medium flex items-center space-x-1 transition">
                <FileVideo className="w-3.5 h-3.5 text-purple-400" />
                <span>Browse File...</span>
                <input type="file" accept="video/mp4,video/x-matroska,video/quicktime" onChange={handleLocalFileSelect} className="hidden" />
              </label>
            </div>

            <label className="flex items-center space-x-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={sourceAuthorized}
                onChange={(e) => setSourceAuthorized(e.target.checked)}
                className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-purple-600 focus:ring-0 cursor-pointer"
              />
              <span className="text-[11px] text-slate-300 font-medium">
                I confirm usage rights or creator authorization for this gaming footage.
              </span>
            </label>
          </div>
        </div>

        {/* Processing Configuration & Publishing Destinations Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 pt-3 border-t border-gaming-border">
          {/* Left: Configuration & Toggles */}
          <div className="lg:col-span-2 space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center space-x-1.5">
                <Sliders className="w-4 h-4 text-purple-400" />
                <span>Processing & Vertical Template</span>
              </span>
              <span className="text-[10px] bg-slate-800 text-slate-400 px-2 py-0.5 rounded font-mono">
                1080×1920 • 9:16 • 60s
              </span>
            </div>

            {/* Template Selector */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {[
                { id: 'blurred_bg', name: 'Blurred Background', desc: 'Gaussian blurred background with centered 16:9 gameplay.' },
                { id: 'dynamic_gameplay', name: 'Dynamic Gameplay', desc: 'Full-bleed 9:16 vertical crop centered on action.' },
                { id: 'split_layout', name: 'Smart Split', desc: 'Dual-zone layout for reaction/facecam & gameplay.' }
              ].map((t) => (
                <div
                  key={t.id}
                  onClick={() => setTemplate(t.id)}
                  className={`p-3 rounded-xl border cursor-pointer transition flex flex-col justify-between space-y-1 ${
                    template === t.id
                      ? 'bg-purple-600/10 border-purple-500 ring-1 ring-purple-500/50 text-purple-200'
                      : 'bg-[#0a0d14] border-gaming-border text-slate-400 hover:border-slate-700'
                  }`}
                >
                  <span className="font-bold text-xs text-slate-200">{t.name}</span>
                  <p className="text-[10px] text-slate-400 leading-tight">{t.desc}</p>
                </div>
              ))}
            </div>

            {/* 8 Feature Toggles */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 pt-1">
              {[
                { label: 'AI Highlights', state: aiHighlightDetection, set: setAiHighlightDetection },
                { label: 'Smart Cropping', state: smartCropping, set: setSmartCropping },
                { label: 'Animated Captions', state: animatedCaptions, set: setAnimatedCaptions },
                { label: 'Watermark Brand', state: watermark, set: setWatermark },
                { label: 'AI Titles', state: aiTitle, set: setAiTitle },
                { label: 'Topic Classifier', state: aiTopic, set: setAiTopic },
                { label: 'AI Description', state: aiDesc, set: setAiDesc },
                { label: 'AI Hashtags', state: aiHashtags, set: setAiHashtags },
              ].map((tog) => (
                <label key={tog.label} className="flex items-center space-x-2 text-[11px] text-slate-300 bg-[#0a0d14] p-2.5 rounded-xl border border-gaming-border cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={tog.state}
                    onChange={(e) => tog.set(e.target.checked)}
                    className="w-3.5 h-3.5 rounded bg-slate-900 border-slate-700 text-purple-600 focus:ring-0"
                  />
                  <span>{tog.label}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Right: Destinations & Live Status */}
          <div className="space-y-3 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between pb-2 mb-2 border-b border-gaming-border">
                <span className="text-xs font-bold uppercase tracking-wider text-slate-300">Publishing Destinations</span>
                <span className="text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/30 px-2 py-0.5 rounded font-semibold">
                  Manual Review Default
                </span>
              </div>

              <div className="space-y-2">
                {[
                  {
                    id: 'youtube',
                    name: 'YouTube Shorts',
                    icon: Youtube,
                    color: 'text-red-500',
                    connected: connections?.youtube?.authenticated,
                    statusText: connections?.youtube?.authenticated
                      ? 'Connected'
                      : connections?.youtube?.client_configured
                      ? 'Not Connected (Channel Auth Required)'
                      : 'Not Connected'
                  },
                  {
                    id: 'instagram',
                    name: 'Instagram Reels',
                    icon: Instagram,
                    color: 'text-pink-500',
                    connected: connections?.instagram?.connected,
                    statusText: connections?.instagram?.connected ? 'Connected' : 'Not Connected'
                  },
                  {
                    id: 'facebook',
                    name: 'Facebook Reels',
                    icon: Facebook,
                    color: 'text-blue-500',
                    connected: connections?.facebook?.connected,
                    statusText: connections?.facebook?.connected ? 'Connected' : 'Not Connected'
                  }
                ].map((p) => {
                  const Icon = p.icon
                  const isSelected = platforms.includes(p.id)
                  return (
                    <div
                      key={p.id}
                      onClick={() => togglePlatform(p.id)}
                      className={`p-2.5 rounded-xl border cursor-pointer flex items-center justify-between transition ${
                        isSelected ? 'bg-slate-900 border-purple-500/50' : 'bg-[#0a0d14] border-gaming-border opacity-70'
                      }`}
                    >
                      <div className="flex items-center space-x-2.5">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => {}}
                          className="w-3.5 h-3.5 rounded bg-slate-900 border-slate-700 text-purple-600 focus:ring-0"
                        />
                        <Icon className={`w-4 h-4 ${p.color}`} />
                        <div>
                          <span className="block text-xs font-semibold text-slate-200">{p.name}</span>
                          <span className="block text-[10px] text-slate-500">{p.statusText}</span>
                        </div>
                      </div>
                      <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${
                        p.connected ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-800 text-slate-500'
                      }`}>
                        {p.connected ? 'READY' : 'NOT CONNECTED'}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            <p className="text-[10px] text-slate-500">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400 inline mr-1" />
              Clips default to Human Review until official publishing API connections are verified.
            </p>
          </div>
        </div>
      </form>

      {/* 10-Stage Real-Time Progress Interface */}
      {submitting && (
        <div className="bg-[#111827] border border-purple-500/40 rounded-2xl p-6 space-y-5 shadow-2xl animate-fade-in">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-bold text-slate-100 flex items-center space-x-2">
                <span>Processing Reel: {jobProgress?.current_step || 'Initializing pipeline...'}</span>
                {jobId && (
                  <span className="text-xs font-mono text-purple-400 bg-purple-500/10 px-2 py-0.5 rounded">
                    Job #{jobId}
                  </span>
                )}
              </h3>
              <p className="text-xs text-slate-400 mt-0.5">Autonomous video pipeline active</p>
            </div>
            <span className="text-2xl font-black font-mono text-purple-400">
              {jobProgress?.progress_pct || 10}%
            </span>
          </div>

          {/* Progress Bar */}
          <div className="w-full bg-[#0a0d14] h-3 rounded-full overflow-hidden border border-slate-800">
            <div
              className="bg-gradient-to-r from-purple-600 via-indigo-500 to-emerald-400 h-full transition-all duration-300"
              style={{ width: `${jobProgress?.progress_pct || 10}%` }}
            />
          </div>

          {/* 10-Stage Stepper Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5 pt-2">
            {[
              "Source validation",
              "Media ingestion",
              "AI video analysis",
              "Highlight detection",
              "Clip composition",
              "Caption generation",
              "Video rendering",
              "Quality validation",
              "Metadata generation",
              "Reel ready"
            ].map((stageName, idx) => {
              const stages = jobProgress?.stages || []
              const st = stages.find((s: any) => s.name === stageName)
              const status = st?.status || 'pending'
              return (
                <div
                  key={stageName}
                  className={`p-2.5 rounded-xl border text-xs flex items-center space-x-2 transition ${
                    status === 'completed'
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300 font-semibold'
                      : status === 'in_progress'
                      ? 'border-purple-500 bg-purple-600/20 text-purple-200 font-bold ring-1 ring-purple-500/50'
                      : status === 'failed'
                      ? 'border-rose-500 bg-rose-500/20 text-rose-300 font-bold'
                      : 'border-slate-800 bg-[#0a0d14] text-slate-500'
                  }`}
                >
                  <span className="w-5 h-5 rounded-full bg-slate-900 border border-slate-700 text-[10px] font-bold flex items-center justify-center">
                    {status === 'completed' ? <Check className="w-3 h-3 text-emerald-400" /> : idx + 1}
                  </span>
                  <span className="text-[11px] truncate">{stageName}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Generated Reel Preview Card */}
      {completedClip && (
        <div className="bg-[#111827] border border-gaming-border rounded-2xl p-6 space-y-6 shadow-2xl animate-fade-in">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between border-b border-gaming-border pb-4 gap-3">
            <div>
              <div className="flex items-center space-x-2">
                <h3 className="text-lg font-black text-slate-100">Generated Reel Preview</h3>
                <span className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs px-2.5 py-0.5 rounded-full font-bold">
                  1080×1920 Ready
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Rendered with NVIDIA NVENC hardware acceleration. Subtitles & Gaming Uncut watermark burned.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <a
                href={completedClip.video_url}
                download={`gaming_uncut_${completedClip.id}.mp4`}
                className="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition"
              >
                <Download className="w-3.5 h-3.5 text-purple-400" />
                <span>Download MP4</span>
              </a>
              <button
                type="button"
                onClick={handleGenerate}
                className="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition"
              >
                <RotateCcw className="w-3.5 h-3.5 text-indigo-400" />
                <span>Regenerate</span>
              </button>
              <button
                type="button"
                onClick={handleAddToQueue}
                className="px-5 py-2 bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white rounded-xl text-xs font-bold shadow-lg shadow-purple-600/30 flex items-center space-x-1.5 transition"
              >
                <Check className="w-4 h-4" />
                <span>Add to Publishing Queue</span>
              </button>
            </div>
          </div>

          {queueSuccessMsg && (
            <div className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs px-4 py-3 rounded-xl flex items-center space-x-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
              <span>{queueSuccessMsg}</span>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-12 gap-6 items-start">
            {/* 9:16 Video Player */}
            <div className="md:col-span-5 flex flex-col items-center">
              <div className="relative w-full max-w-[280px] aspect-vertical bg-black rounded-2xl overflow-hidden border border-slate-800 shadow-2xl flex items-center justify-center">
                <video src={completedClip.video_url} controls className="w-full h-full object-contain" />
                <div className="absolute top-2.5 left-2.5 bg-black/70 backdrop-blur-md px-2 py-0.5 rounded text-[10px] font-mono font-bold text-white flex items-center space-x-1 border border-white/10">
                  <Clock className="w-3 h-3 text-purple-400" />
                  <span>{completedClip.duration || 60.0}s</span>
                </div>
                <div className="absolute top-2.5 right-2.5 bg-purple-600/80 backdrop-blur-md px-2 py-0.5 rounded text-[10px] font-mono font-bold text-white flex items-center space-x-1 border border-purple-400/30">
                  <span>{completedClip.ai_highlight_score || 88}% Hype</span>
                </div>
              </div>
              <div className="flex items-center space-x-3 mt-3 text-xs text-slate-400">
                <span>{completedClip.resolution || '1080×1920'}</span>
                <span>•</span>
                <span>{completedClip.layout_template || 'blurred_bg'}</span>
              </div>
            </div>

            {/* AI Metadata & Editor */}
            <div className="md:col-span-7 space-y-4 bg-[#0a0d14] border border-gaming-border rounded-2xl p-5">
              <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                <h4 className="text-xs font-bold uppercase tracking-wider text-purple-400 flex items-center space-x-1.5">
                  <Sparkles className="w-3.5 h-3.5" />
                  <span>AI Generated Publishing Metadata</span>
                </h4>
                <button
                  type="button"
                  onClick={() => setIsEditingMeta(!isEditingMeta)}
                  className="text-xs text-slate-400 hover:text-purple-400 flex items-center space-x-1 transition"
                >
                  <Edit3 className="w-3.5 h-3.5" />
                  <span>{isEditingMeta ? 'Cancel' : 'Edit Metadata'}</span>
                </button>
              </div>

              {saveSuccessMsg && (
                <div className="text-xs text-emerald-400 font-semibold">{saveSuccessMsg}</div>
              )}

              <div className="space-y-3">
                <div>
                  <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wide mb-1">Title</label>
                  <input
                    type="text"
                    disabled={!isEditingMeta}
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    className="w-full bg-[#111827] border border-slate-800 focus:border-purple-500 rounded-xl px-3.5 py-2 text-xs text-slate-100 font-semibold transition disabled:opacity-80"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wide mb-1">Topic</label>
                    <input
                      type="text"
                      disabled={!isEditingMeta}
                      value={editTopic}
                      onChange={(e) => setEditTopic(e.target.value)}
                      className="w-full bg-[#111827] border border-slate-800 focus:border-purple-500 rounded-xl px-3.5 py-2 text-xs text-slate-200 transition disabled:opacity-80"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wide mb-1">Hashtags</label>
                    <input
                      type="text"
                      disabled={!isEditingMeta}
                      value={editHashtags}
                      onChange={(e) => setEditHashtags(e.target.value)}
                      className="w-full bg-[#111827] border border-slate-800 focus:border-purple-500 rounded-xl px-3.5 py-2 text-xs text-purple-300 font-mono transition disabled:opacity-80"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-[11px] font-bold text-slate-400 uppercase tracking-wide mb-1">Description</label>
                  <textarea
                    rows={3}
                    disabled={!isEditingMeta}
                    value={editDesc}
                    onChange={(e) => setEditDesc(e.target.value)}
                    className="w-full bg-[#111827] border border-slate-800 focus:border-purple-500 rounded-xl px-3.5 py-2 text-xs text-slate-300 transition disabled:opacity-80 leading-relaxed"
                  />
                </div>

                {isEditingMeta && (
                  <div className="flex justify-end pt-2">
                    <button
                      type="button"
                      onClick={handleSaveMetadata}
                      className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl text-xs font-bold transition flex items-center space-x-1.5 shadow-md shadow-emerald-600/30"
                    >
                      <Save className="w-3.5 h-3.5" />
                      <span>Save Changes</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
