import React, { useState, useEffect } from 'react'
import {
  TrendingUp, Sparkles, Youtube, Instagram, Facebook, ShieldCheck,
  CheckCircle2, AlertCircle, Copy, RotateCw, Check, ArrowRight,
  Download, Filter, Search, Award, BarChart3, Eye, Zap, Layers,
  Lightbulb, ExternalLink, RefreshCw
} from 'lucide-react'
import {
  fetchSeoDashboard, fetchClipSeo, generateClipSeo, approveClipSeo,
  bulkGenerateSeo, bulkApproveSeo, computeGrowthInsights, fetchClips
} from '../services/api'
import { GeneratedClipItem } from '../types'

export const SeoGrowthPage: React.FC = () => {
  // Dashboard states
  const [dashboard, setDashboard] = useState<any>(null)
  const [loadingDashboard, setLoadingDashboard] = useState(false)
  const [computingInsights, setComputingInsights] = useState(false)

  // Clips and selection states
  const [clips, setClips] = useState<GeneratedClipItem[]>([])
  const [selectedClipId, setSelectedClipId] = useState<number | null>(null)
  const [clipSeo, setClipSeo] = useState<any>(null)
  const [loadingClipSeo, setLoadingClipSeo] = useState(false)
  const [activePlatformTab, setActivePlatformTab] = useState<'youtube' | 'instagram' | 'facebook'>('youtube')

  // Bulk operation states
  const [selectedClipIds, setSelectedClipIds] = useState<number[]>([])
  const [bulkActionLoading, setBulkActionLoading] = useState(false)
  const [actionSuccessMsg, setActionSuccessMsg] = useState('')
  const [copiedField, setCopiedField] = useState<string | null>(null)

  // Filtering states
  const [searchTerm, setSearchTerm] = useState('')
  const [gameFilter, setGameFilter] = useState('all')
  const [minScoreFilter, setMinScoreFilter] = useState<number>(0)

  // Load initial data
  const loadDashboardData = async () => {
    setLoadingDashboard(true)
    try {
      const [dashData, clipsData] = await Promise.all([
        fetchSeoDashboard(),
        fetchClips()
      ])
      setDashboard(dashData)
      setClips(clipsData)
      if (clipsData.length > 0 && selectedClipId === null) {
        setSelectedClipId(clipsData[0].id)
      }
    } catch (err) {
      console.error('Error loading SEO dashboard:', err)
    } finally {
      setLoadingDashboard(false)
    }
  }

  useEffect(() => {
    loadDashboardData()
  }, [])

  // Load selected clip's SEO package
  useEffect(() => {
    if (selectedClipId !== null) {
      setLoadingClipSeo(true)
      fetchClipSeo(selectedClipId)
        .then((data) => {
          setClipSeo(data)
        })
        .catch((err) => console.error('Error loading clip SEO:', err))
        .finally(() => setLoadingClipSeo(false))
    }
  }, [selectedClipId])

  const handleCopy = (text: string, fieldId: string) => {
    navigator.clipboard.writeText(text)
    setCopiedField(fieldId)
    setTimeout(() => setCopiedField(null), 2500)
  }

  const handleRegenerateCurrent = async (platform?: string) => {
    if (!selectedClipId) return
    setLoadingClipSeo(true)
    try {
      const res = await generateClipSeo(selectedClipId, {
        platforms: platform ? [platform] : ['youtube', 'instagram', 'facebook'],
        bypass_cache: true
      })
      setClipSeo(res)
      setActionSuccessMsg(`Fresh SEO package regenerated for ${platform || 'all platforms'}!`)
      setTimeout(() => setActionSuccessMsg(''), 4000)
      loadDashboardData()
    } catch (err: any) {
      alert('SEO generation failed: ' + err.message)
    } finally {
      setLoadingClipSeo(false)
    }
  }

  const handleApproveCurrent = async (platform: string) => {
    if (!selectedClipId) return
    try {
      await approveClipSeo(selectedClipId, platform)
      setActionSuccessMsg(`Approved SEO metadata for ${platform.toUpperCase()}! Synchronized with publishing queue.`)
      setTimeout(() => setActionSuccessMsg(''), 4000)
      const updated = await fetchClipSeo(selectedClipId)
      setClipSeo(updated)
      loadDashboardData()
    } catch (err: any) {
      alert('Approval failed: ' + err.message)
    }
  }

  const handleComputeInsights = async () => {
    setComputingInsights(true)
    try {
      await computeGrowthInsights()
      await loadDashboardData()
      setActionSuccessMsg('Continuous learning feedback loop executed! Fresh correlation patterns updated.')
      setTimeout(() => setActionSuccessMsg(''), 4000)
    } catch (err: any) {
      alert('Failed to compute insights: ' + err.message)
    } finally {
      setComputingInsights(false)
    }
  }

  const handleBulkGenerate = async () => {
    if (selectedClipIds.length === 0) return
    if (!confirm(`Generate SEO packages for ${selectedClipIds.length} selected clips?`)) return
    setBulkActionLoading(true)
    try {
      await bulkGenerateSeo({ clip_ids: selectedClipIds, bypass_cache: true })
      setActionSuccessMsg(`Successfully generated SEO packages for ${selectedClipIds.length} clips!`)
      setTimeout(() => setActionSuccessMsg(''), 4000)
      setSelectedClipIds([])
      if (selectedClipId) {
        const updated = await fetchClipSeo(selectedClipId)
        setClipSeo(updated)
      }
      loadDashboardData()
    } catch (err: any) {
      alert('Bulk generation failed: ' + err.message)
    } finally {
      setBulkActionLoading(false)
    }
  }

  const handleBulkApprove = async () => {
    if (selectedClipIds.length === 0) return
    if (!confirm(`Approve all platform SEO packages for ${selectedClipIds.length} selected clips?`)) return
    setBulkActionLoading(true)
    try {
      await bulkApproveSeo({ clip_ids: selectedClipIds, platform: 'all' })
      setActionSuccessMsg(`Successfully approved SEO metadata for ${selectedClipIds.length} clips!`)
      setTimeout(() => setActionSuccessMsg(''), 4000)
      setSelectedClipIds([])
      if (selectedClipId) {
        const updated = await fetchClipSeo(selectedClipId)
        setClipSeo(updated)
      }
      loadDashboardData()
    } catch (err: any) {
      alert('Bulk approval failed: ' + err.message)
    } finally {
      setBulkActionLoading(false)
    }
  }

  const toggleSelectClip = (id: number) => {
    setSelectedClipIds((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    )
  }

  const toggleSelectAll = () => {
    if (selectedClipIds.length === filteredClips.length) {
      setSelectedClipIds([])
    } else {
      setSelectedClipIds(filteredClips.map((c) => c.id))
    }
  }

  // Filtered clips
  const filteredClips = clips.filter((c) => {
    const matchesSearch =
      (c.title || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
      (c.detected_game || '').toLowerCase().includes(searchTerm.toLowerCase())
    const matchesGame = gameFilter === 'all' || (c.detected_game || 'Unknown Game') === gameFilter
    const matchesScore = (c.quality_score || 0) >= minScoreFilter
    return matchesSearch && matchesGame && matchesScore
  })

  const uniqueGames = Array.from(new Set(clips.map((c) => c.detected_game || 'Unknown Game')))
  const currentPlatformSeo = clipSeo?.platforms?.[activePlatformTab]

  return (
    <div className="max-w-7xl mx-auto space-y-8 pb-16">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between pb-4 border-b border-gaming-border gap-4">
        <div>
          <div className="flex items-center space-x-3">
            <h1 className="text-2xl font-black font-brand tracking-wide text-slate-100 flex items-center space-x-2.5">
              <TrendingUp className="w-6 h-6 text-purple-400" />
              <span>AI SEO & GROWTH INTELLIGENCE</span>
            </h1>
            <span className="text-[11px] bg-gradient-to-r from-purple-500/20 to-indigo-500/20 text-purple-300 border border-purple-500/30 px-3 py-0.5 rounded-full font-bold uppercase tracking-wider">
              Local-First AI Engine
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Autonomous multi-platform SEO optimization for YouTube Shorts, Instagram Reels, and Facebook Reels. Powered by grounded gameplay intelligence and continuous learning.
          </p>
        </div>

        <div className="flex items-center space-x-2.5">
          <button
            onClick={handleComputeInsights}
            disabled={computingInsights}
            className="px-4 py-2 bg-purple-600/20 hover:bg-purple-600/30 text-purple-300 border border-purple-500/30 rounded-xl text-xs font-bold flex items-center space-x-2 transition disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${computingInsights ? 'animate-spin' : ''}`} />
            <span>{computingInsights ? 'Computing Insights...' : 'Analyze Growth Patterns'}</span>
          </button>
          <button
            onClick={loadDashboardData}
            disabled={loadingDashboard}
            className="px-3.5 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition"
          >
            <RotateCw className={`w-3.5 h-3.5 ${loadingDashboard ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {actionSuccessMsg && (
        <div className="bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs px-4 py-3 rounded-xl flex items-center space-x-2 shadow-lg animate-fade-in">
          <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          <span>{actionSuccessMsg}</span>
        </div>
      )}

      {/* 1. SEO Dashboard Section */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* KPI 1: Optimized Clips */}
        <div className="bg-[#111827] border border-gaming-border rounded-2xl p-5 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400 font-bold uppercase tracking-wider">
            <span>Clips Optimized</span>
            <Layers className="w-4 h-4 text-purple-400" />
          </div>
          <div className="flex items-baseline space-x-2">
            <span className="text-3xl font-black text-slate-100 font-mono">
              {dashboard?.optimized_clips ?? 0}
            </span>
            <span className="text-xs text-slate-500 font-mono">
              / {dashboard?.total_clips ?? 0} total
            </span>
          </div>
          <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
            <div
              className="bg-purple-500 h-full"
              style={{
                width: `${Math.min(100, Math.round(((dashboard?.optimized_clips ?? 0) / Math.max(1, dashboard?.total_clips ?? 1)) * 100))}%`
              }}
            />
          </div>
        </div>

        {/* KPI 2: Average SEO Score */}
        <div className="bg-[#111827] border border-gaming-border rounded-2xl p-5 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400 font-bold uppercase tracking-wider">
            <span>Avg SEO Score</span>
            <Award className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="flex items-baseline space-x-2">
            <span className="text-3xl font-black text-emerald-400 font-mono">
              {dashboard?.average_seo_score ?? 0}
            </span>
            <span className="text-xs text-slate-500 font-mono">/ 100</span>
          </div>
          <p className="text-[11px] text-slate-400">
            Internal 7-dimension content quality index
          </p>
        </div>

        {/* KPI 3: YouTube Shorts Readiness */}
        <div className="bg-[#111827] border border-gaming-border rounded-2xl p-5 space-y-2">
          <div className="flex items-center justify-between text-xs text-red-400 font-bold uppercase tracking-wider">
            <span className="flex items-center space-x-1">
              <Youtube className="w-3.5 h-3.5" />
              <span>YouTube Shorts</span>
            </span>
            <span className="text-[11px] font-mono text-slate-400">
              {dashboard?.platform_breakdown?.youtube?.avg_score ?? 0}% Avg
            </span>
          </div>
          <div className="flex items-baseline space-x-2">
            <span className="text-2xl font-black text-slate-100 font-mono">
              {dashboard?.platform_breakdown?.youtube?.approved_count ?? 0}
            </span>
            <span className="text-xs text-emerald-400 font-semibold">Approved</span>
            <span className="text-xs text-slate-500">
              ({dashboard?.platform_breakdown?.youtube?.pending_count ?? 0} pending)
            </span>
          </div>
          <p className="text-[11px] text-slate-400">5 distinct titles & search intent</p>
        </div>

        {/* KPI 4: Instagram Reels Readiness */}
        <div className="bg-[#111827] border border-gaming-border rounded-2xl p-5 space-y-2">
          <div className="flex items-center justify-between text-xs text-pink-400 font-bold uppercase tracking-wider">
            <span className="flex items-center space-x-1">
              <Instagram className="w-3.5 h-3.5" />
              <span>Instagram Reels</span>
            </span>
            <span className="text-[11px] font-mono text-slate-400">
              {dashboard?.platform_breakdown?.instagram?.avg_score ?? 0}% Avg
            </span>
          </div>
          <div className="flex items-baseline space-x-2">
            <span className="text-2xl font-black text-slate-100 font-mono">
              {dashboard?.platform_breakdown?.instagram?.approved_count ?? 0}
            </span>
            <span className="text-xs text-pink-400 font-semibold">Approved</span>
            <span className="text-xs text-slate-500">
              ({dashboard?.platform_breakdown?.instagram?.pending_count ?? 0} pending)
            </span>
          </div>
          <p className="text-[11px] text-slate-400">3 caption styles & max-5 tags</p>
        </div>
      </div>

      {/* Top Keywords & Continuous Learning Insights */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Top Keywords Pill Box */}
        <div className="lg:col-span-5 bg-[#111827] border border-gaming-border rounded-2xl p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center space-x-2">
              <Sparkles className="w-4 h-4 text-purple-400" />
              <span>Top Discovery Keywords</span>
            </h3>
            <span className="text-[10px] text-slate-500 font-mono">Live Search & DB</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {(dashboard?.top_keywords || []).map((k: any, idx: number) => (
              <span
                key={idx}
                className="text-xs bg-[#0a0d14] border border-gaming-border text-slate-200 px-3 py-1 rounded-xl flex items-center space-x-1.5"
              >
                <span>{k.keyword}</span>
                <span className="text-[10px] bg-purple-500/20 text-purple-300 font-mono px-1.5 py-0.2 rounded font-bold">
                  {k.frequency}
                </span>
              </span>
            ))}
            {(dashboard?.top_keywords || []).length === 0 && (
              <p className="text-xs text-slate-500 italic">Generate SEO packages to populate keyword research index.</p>
            )}
          </div>
        </div>

        {/* Continuous Learning Growth Findings */}
        <div className="lg:col-span-7 bg-[#111827] border border-gaming-border rounded-2xl p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-bold uppercase tracking-wider text-slate-300 flex items-center space-x-2">
              <Lightbulb className="w-4 h-4 text-amber-400" />
              <span>AI Growth Intelligence & Insights</span>
            </h3>
            <span className="text-[10px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 rounded font-mono">
              Learning Loop Active
            </span>
          </div>
          <div className="space-y-2">
            {(dashboard?.growth_insights || []).map((item: any, idx: number) => (
              <div
                key={idx}
                className="bg-[#0a0d14] border border-slate-800 rounded-xl p-3 text-xs space-y-1 hover:border-slate-700 transition"
              >
                <div className="flex items-center justify-between">
                  <span className="font-bold text-slate-200">{item.dimension}</span>
                  <span className="text-[10px] font-mono text-purple-400 bg-purple-950/40 px-2 py-0.5 rounded border border-purple-800/40">
                    N={item.sample_size}
                  </span>
                </div>
                <p className="text-slate-400 text-[11px] leading-relaxed">{item.summary}</p>
                {item.recommendation && (
                  <p className="text-emerald-400 font-medium text-[11px]">
                    Recommendation: <span className="text-slate-300">{item.recommendation}</span>
                  </p>
                )}
              </div>
            ))}
            {(dashboard?.growth_insights || []).length === 0 && (
              <p className="text-xs text-slate-500 italic">Click 'Analyze Growth Patterns' to derive patterns from historical clips.</p>
            )}
          </div>
        </div>
      </div>

      {/* 2. Clip SEO Workspace */}
      <div className="bg-[#111827] border border-gaming-border rounded-2xl p-6 space-y-6 shadow-2xl">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-gaming-border gap-4">
          <div>
            <h2 className="text-lg font-black text-slate-100 flex items-center space-x-2">
              <span>Clip SEO Workspace</span>
              {clipSeo?.game_mismatch_flag && (
                <span className="bg-amber-500/20 text-amber-300 border border-amber-500/30 text-[10px] px-2.5 py-0.5 rounded-full font-bold">
                  Game Mismatch Flagged
                </span>
              )}
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Select any generated clip from your library to inspect, edit, and approve platform-tailored SEO metadata.
            </p>
          </div>

          <div className="flex items-center space-x-2.5">
            <select
              value={selectedClipId ?? ''}
              onChange={(e) => setSelectedClipId(Number(e.target.value))}
              className="bg-[#0a0d14] border border-slate-700 rounded-xl px-3 py-2 text-xs text-slate-200 font-medium focus:border-purple-500 focus:outline-none"
            >
              {clips.map((c) => (
                <option key={c.id} value={c.id}>
                  Clip #{c.id} — {c.title.slice(0, 40)} ({c.detected_game || 'Unknown'})
                </option>
              ))}
            </select>
            <button
              onClick={() => handleRegenerateCurrent()}
              disabled={loadingClipSeo}
              className="px-3.5 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-xl text-xs font-bold shadow-lg shadow-purple-600/30 flex items-center space-x-1.5 transition disabled:opacity-50"
            >
              <RotateCw className={`w-3.5 h-3.5 ${loadingClipSeo ? 'animate-spin' : ''}`} />
              <span>Regenerate All</span>
            </button>
          </div>
        </div>

        {/* 3 Platform Tabs Switcher */}
        <div className="flex items-center space-x-2 border-b border-gaming-border pb-3">
          <button
            onClick={() => setActivePlatformTab('youtube')}
            className={`px-4 py-2.5 rounded-xl text-xs font-bold flex items-center space-x-2 transition ${
              activePlatformTab === 'youtube'
                ? 'bg-red-500/20 text-red-300 border border-red-500/40 shadow-sm'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            }`}
          >
            <Youtube className="w-4 h-4 text-red-400" />
            <span>YouTube Shorts SEO</span>
            {clipSeo?.platforms?.youtube?.seo_score && (
              <span className="text-[10px] font-mono bg-red-950/60 text-red-300 px-2 py-0.5 rounded font-bold border border-red-800/40">
                {clipSeo.platforms.youtube.seo_score}%
              </span>
            )}
          </button>

          <button
            onClick={() => setActivePlatformTab('instagram')}
            className={`px-4 py-2.5 rounded-xl text-xs font-bold flex items-center space-x-2 transition ${
              activePlatformTab === 'instagram'
                ? 'bg-pink-500/20 text-pink-300 border border-pink-500/40 shadow-sm'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            }`}
          >
            <Instagram className="w-4 h-4 text-pink-400" />
            <span>Instagram Reels SEO</span>
            {clipSeo?.platforms?.instagram?.seo_score && (
              <span className="text-[10px] font-mono bg-pink-950/60 text-pink-300 px-2 py-0.5 rounded font-bold border border-pink-800/40">
                {clipSeo.platforms.instagram.seo_score}%
              </span>
            )}
          </button>

          <button
            onClick={() => setActivePlatformTab('facebook')}
            className={`px-4 py-2.5 rounded-xl text-xs font-bold flex items-center space-x-2 transition ${
              activePlatformTab === 'facebook'
                ? 'bg-blue-500/20 text-blue-300 border border-blue-500/40 shadow-sm'
                : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
            }`}
          >
            <Facebook className="w-4 h-4 text-blue-400" />
            <span>Facebook Reels SEO</span>
            {clipSeo?.platforms?.facebook?.seo_score && (
              <span className="text-[10px] font-mono bg-blue-950/60 text-blue-300 px-2 py-0.5 rounded font-bold border border-blue-800/40">
                {clipSeo.platforms.facebook.seo_score}%
              </span>
            )}
          </button>
        </div>

        {/* Tab Content: Platform SEO Details & Live Preview Mockup */}
        {currentPlatformSeo ? (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
            {/* Left 7 Cols: SEO Controls, Titles, Keywords, Descriptions */}
            <div className="lg:col-span-7 space-y-5">
              {/* Recommended Title / Caption */}
              <div className="bg-[#0a0d14] border border-gaming-border rounded-2xl p-5 space-y-3">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-bold uppercase tracking-wider text-purple-400 flex items-center space-x-1.5">
                    <span>Recommended {activePlatformTab === 'youtube' ? 'SEO Title' : 'Caption'}</span>
                  </label>
                  <button
                    onClick={() => handleCopy(currentPlatformSeo.recommended_title || currentPlatformSeo.recommended_caption, 'rec_title')}
                    className="text-xs text-slate-400 hover:text-purple-400 flex items-center space-x-1 transition"
                  >
                    {copiedField === 'rec_title' ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedField === 'rec_title' ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
                <div className="text-sm font-bold text-slate-100 bg-[#111827] border border-slate-800 rounded-xl p-3.5">
                  {currentPlatformSeo.recommended_title || currentPlatformSeo.recommended_caption}
                </div>

                {/* Candidate Variations */}
                {(currentPlatformSeo.candidate_titles || currentPlatformSeo.caption_variations || currentPlatformSeo.description_variations) && (
                  <div className="space-y-1.5 pt-2">
                    <span className="text-[11px] font-bold text-slate-400 uppercase">Distinct Variations</span>
                    <div className="space-y-1.5">
                      {(currentPlatformSeo.candidate_titles || currentPlatformSeo.caption_variations || currentPlatformSeo.description_variations || []).map((t: string, idx: number) => (
                        <div
                          key={idx}
                          className="text-xs text-slate-300 bg-[#121826] border border-slate-800/80 hover:border-purple-500/40 rounded-xl p-2.5 flex items-center justify-between transition group"
                        >
                          <span className="truncate pr-2">{t}</span>
                          <button
                            onClick={() => handleCopy(t, `var_${idx}`)}
                            className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-purple-400 p-1 transition"
                          >
                            {copiedField === `var_${idx}` ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Description & Prose */}
              <div className="bg-[#0a0d14] border border-gaming-border rounded-2xl p-5 space-y-2.5">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-bold uppercase tracking-wider text-purple-400">
                    Search-Indexed Description
                  </label>
                  <button
                    onClick={() => handleCopy(currentPlatformSeo.description || currentPlatformSeo.recommended_caption, 'desc')}
                    className="text-xs text-slate-400 hover:text-purple-400 flex items-center space-x-1 transition"
                  >
                    {copiedField === 'desc' ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{copiedField === 'desc' ? 'Copied' : 'Copy'}</span>
                  </button>
                </div>
                <div className="text-xs text-slate-300 bg-[#111827] border border-slate-800 rounded-xl p-3.5 whitespace-pre-line leading-relaxed font-mono">
                  {currentPlatformSeo.description || currentPlatformSeo.recommended_caption}
                </div>
              </div>

              {/* Targeted Keywords & Hashtags */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="bg-[#0a0d14] border border-gaming-border rounded-2xl p-4 space-y-2">
                  <span className="text-xs font-bold uppercase tracking-wider text-slate-400">Target Keywords</span>
                  <div className="space-y-1">
                    <div className="text-xs font-bold text-emerald-300 bg-emerald-950/30 border border-emerald-500/30 px-2.5 py-1 rounded-lg">
                      Primary: {currentPlatformSeo.primary_keyword || 'Gaming'}
                    </div>
                    {(currentPlatformSeo.secondary_keywords || []).map((sk: string, idx: number) => (
                      <div key={idx} className="text-xs text-slate-300 bg-[#111827] px-2.5 py-1 rounded-lg border border-slate-800">
                        {sk}
                      </div>
                    ))}
                  </div>
                </div>

                <div className="bg-[#0a0d14] border border-gaming-border rounded-2xl p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold uppercase tracking-wider text-slate-400">
                      Target Hashtags ({currentPlatformSeo.hashtags?.length || 0})
                    </span>
                    <button
                      onClick={() => handleCopy((currentPlatformSeo.hashtags || []).join(' '), 'tags')}
                      className="text-[11px] text-purple-400 hover:underline"
                    >
                      Copy All
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {(currentPlatformSeo.hashtags || []).map((tag: string, idx: number) => (
                      <span key={idx} className="text-xs bg-[#111827] text-purple-300 border border-purple-500/30 px-2 py-0.5 rounded-lg font-mono">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              {/* Engagement Hook & CTA */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {currentPlatformSeo.opening_hook && (
                  <div className="bg-[#0a0d14] border border-gaming-border rounded-xl p-3.5 space-y-1">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">0-3s Opening Hook</span>
                    <p className="text-xs text-slate-200">{currentPlatformSeo.opening_hook}</p>
                  </div>
                )}
                {currentPlatformSeo.thumbnail_hook_text && (
                  <div className="bg-[#0a0d14] border border-gaming-border rounded-xl p-3.5 space-y-1">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">Suggested Thumbnail Text</span>
                    <p className="text-xs text-emerald-400 font-bold uppercase">{currentPlatformSeo.thumbnail_hook_text}</p>
                  </div>
                )}
                {currentPlatformSeo.onscreen_text && (
                  <div className="bg-[#0a0d14] border border-gaming-border rounded-xl p-3.5 space-y-1">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">On-Screen Overlay Text</span>
                    <p className="text-xs text-pink-400 font-bold">{currentPlatformSeo.onscreen_text}</p>
                  </div>
                )}
                {currentPlatformSeo.cta && (
                  <div className="bg-[#0a0d14] border border-gaming-border rounded-xl p-3.5 space-y-1">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">Engagement CTA</span>
                    <p className="text-xs text-slate-200">{currentPlatformSeo.cta}</p>
                  </div>
                )}
              </div>
            </div>

            {/* Right 5 Cols: Platform Preview Mockup & Transparent Scorecard */}
            <div className="lg:col-span-5 space-y-6">
              {/* Realistic Platform Mockup */}
              <div className="bg-[#0a0d14] border border-gaming-border rounded-2xl p-5 space-y-3">
                <div className="flex items-center justify-between text-xs font-bold text-slate-400 uppercase tracking-wider">
                  <span>Platform Discovery Preview</span>
                  <span className="text-purple-400 font-mono text-[10px]">{activePlatformTab.toUpperCase()}</span>
                </div>

                {activePlatformTab === 'youtube' && (
                  <div className="bg-[#18181b] border border-zinc-800 rounded-xl overflow-hidden shadow-2xl">
                    <div className="aspect-video bg-black flex items-center justify-center relative">
                      {clipSeo.thumbnail_url ? (
                        <img src={clipSeo.thumbnail_url} alt="Thumbnail" className="w-full h-full object-cover" />
                      ) : (
                        <span className="text-zinc-600 text-xs font-mono">1080×1920 Vertical Feed</span>
                      )}
                      <span className="absolute bottom-2 right-2 bg-black/80 text-[10px] font-mono text-white px-1.5 py-0.5 rounded">
                        0:58
                      </span>
                    </div>
                    <div className="p-3.5 space-y-2">
                      <h4 className="text-xs font-bold text-zinc-100 line-clamp-2">
                        {currentPlatformSeo.recommended_title}
                      </h4>
                      <div className="flex items-center space-x-2 text-[11px] text-zinc-400">
                        <span className="font-semibold text-zinc-200">Gaming Uncut</span>
                        <span>•</span>
                        <span>YouTube Shorts</span>
                      </div>
                    </div>
                  </div>
                )}

                {activePlatformTab === 'instagram' && (
                  <div className="bg-[#121212] border border-zinc-800 rounded-xl p-4 space-y-3 font-sans text-xs">
                    <div className="flex items-center space-x-2.5">
                      <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-amber-500 to-pink-500 flex items-center justify-center font-bold text-white text-[11px]">
                        GU
                      </div>
                      <div>
                        <div className="font-bold text-zinc-100">gaminguncut</div>
                        <div className="text-[10px] text-zinc-400">Original audio • {clipSeo.game_name}</div>
                      </div>
                    </div>
                    <div className="text-zinc-200 text-xs whitespace-pre-line line-clamp-4 leading-relaxed">
                      {currentPlatformSeo.recommended_caption}
                    </div>
                  </div>
                )}

                {activePlatformTab === 'facebook' && (
                  <div className="bg-[#242526] border border-zinc-700 rounded-xl p-4 space-y-3 text-xs">
                    <div className="flex items-center space-x-2">
                      <div className="w-7 h-7 rounded-full bg-blue-600 flex items-center justify-center font-bold text-white text-[10px]">
                        f
                      </div>
                      <span className="font-bold text-zinc-100">Gaming Uncut</span>
                      <CheckCircle2 className="w-3.5 h-3.5 text-blue-400" />
                    </div>
                    <p className="text-zinc-200 whitespace-pre-line line-clamp-3">
                      {currentPlatformSeo.description || currentPlatformSeo.recommended_description}
                    </p>
                  </div>
                )}
              </div>

              {/* Transparent 7-Dimension Scorecard */}
              <div className="bg-[#0a0d14] border border-gaming-border rounded-2xl p-5 space-y-4">
                <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                  <div>
                    <h4 className="text-xs font-bold uppercase tracking-wider text-slate-300">
                      SEO Quality Scorecard
                    </h4>
                    <span className="text-[10px] text-slate-500">Internal Content Optimization Index</span>
                  </div>
                  <div className="flex items-baseline space-x-1">
                    <span className="text-2xl font-black text-emerald-400 font-mono">
                      {currentPlatformSeo.seo_score}
                    </span>
                    <span className="text-xs text-slate-500 font-mono">/ 100</span>
                  </div>
                </div>

                {/* Individual Dimension Bars */}
                <div className="space-y-2.5 text-xs">
                  {Object.entries(currentPlatformSeo.score_breakdown || {}).map(([dim, val]: any) => {
                    const label = dim.replace(/_/g, ' ')
                    const maxVal = dim === 'keyword_relevance' ? 25 : dim === 'gameplay_accuracy' ? 20 : dim.includes('quality') || dim.includes('relevance') ? 15 : dim.includes('intent') || dim.includes('hashtag') ? 10 : 5
                    const pct = Math.min(100, Math.round((val / maxVal) * 100))

                    return (
                      <div key={dim} className="space-y-1">
                        <div className="flex items-center justify-between text-[11px]">
                          <span className="capitalize text-slate-400">{label}</span>
                          <span className="font-mono text-slate-200">{val} / {maxVal}</span>
                        </div>
                        <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
                          <div className="bg-emerald-500 h-full" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    )
                  })}
                </div>

                {/* Warnings & Suggestions */}
                {currentPlatformSeo.warnings && currentPlatformSeo.warnings.length > 0 && (
                  <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-3 text-xs space-y-1">
                    <div className="flex items-center space-x-1.5 font-bold text-amber-400">
                      <AlertCircle className="w-3.5 h-3.5" />
                      <span>Optimization Warnings</span>
                    </div>
                    {currentPlatformSeo.warnings.map((w: string, i: number) => (
                      <p key={i} className="text-amber-300 text-[11px] leading-relaxed">• {w}</p>
                    ))}
                  </div>
                )}

                {/* Approval & Action Buttons */}
                <div className="pt-2 flex items-center space-x-3">
                  <button
                    onClick={() => handleApproveCurrent(activePlatformTab)}
                    className="flex-1 py-2.5 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white rounded-xl text-xs font-bold shadow-lg shadow-emerald-600/20 flex items-center justify-center space-x-2 transition"
                  >
                    <Check className="w-4 h-4" />
                    <span>Approve {activePlatformTab.toUpperCase()} Package</span>
                  </button>

                  <button
                    onClick={() => handleRegenerateCurrent(activePlatformTab)}
                    disabled={loadingClipSeo}
                    className="px-3 py-2.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-semibold border border-slate-700 transition"
                  >
                    <RotateCw className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="bg-[#0a0d14] border border-gaming-border rounded-xl p-12 text-center text-slate-500 text-xs">
            {loadingClipSeo ? 'Analyzing video telemetry and generating platform SEO...' : 'No SEO metadata available for this clip.'}
          </div>
        )}
      </div>

      {/* 3. Bulk SEO Operations Table */}
      <div className="bg-[#111827] border border-gaming-border rounded-2xl p-6 space-y-5 shadow-2xl">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-4 border-b border-gaming-border gap-4">
          <div>
            <h2 className="text-lg font-black text-slate-100 flex items-center space-x-2">
              <span>Bulk SEO Operations & Library Index</span>
              {selectedClipIds.length > 0 && (
                <span className="bg-purple-500/20 text-purple-300 border border-purple-500/30 text-[10px] px-2.5 py-0.5 rounded-full font-bold">
                  {selectedClipIds.length} Selected
                </span>
              )}
            </h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Generate or approve platform SEO packages in bulk across your library.
            </p>
          </div>

          {/* Bulk Action Controls */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={handleBulkGenerate}
              disabled={selectedClipIds.length === 0 || bulkActionLoading}
              className="px-3.5 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-xl text-xs font-bold shadow-md shadow-purple-600/30 flex items-center space-x-1.5 transition disabled:opacity-40"
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>Bulk Generate ({selectedClipIds.length})</span>
            </button>
            <button
              onClick={handleBulkApprove}
              disabled={selectedClipIds.length === 0 || bulkActionLoading}
              className="px-3.5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl text-xs font-bold shadow-md shadow-emerald-600/30 flex items-center space-x-1.5 transition disabled:opacity-40"
            >
              <Check className="w-3.5 h-3.5" />
              <span>Bulk Approve ({selectedClipIds.length})</span>
            </button>
            <a
              href="/api/seo/export?format=csv"
              className="px-3 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded-xl text-xs font-semibold flex items-center space-x-1.5 transition"
            >
              <Download className="w-3.5 h-3.5 text-purple-400" />
              <span>Export CSV</span>
            </a>
          </div>
        </div>

        {/* Filter & Search Bar */}
        <div className="grid grid-cols-1 sm:grid-cols-12 gap-3 text-xs">
          <div className="sm:col-span-6 relative">
            <Search className="w-3.5 h-3.5 absolute left-3 top-3 text-slate-500" />
            <input
              type="text"
              placeholder="Search by clip title or game..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl pl-9 pr-3 py-2 text-slate-100 placeholder-slate-500 focus:border-purple-500 focus:outline-none"
            />
          </div>

          <div className="sm:col-span-3">
            <select
              value={gameFilter}
              onChange={(e) => setGameFilter(e.target.value)}
              className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl px-3 py-2 text-slate-300 focus:border-purple-500 focus:outline-none"
            >
              <option value="all">All Games ({uniqueGames.length})</option>
              {uniqueGames.map((g) => (
                <option key={g} value={g}>{g}</option>
              ))}
            </select>
          </div>

          <div className="sm:col-span-3">
            <select
              value={minScoreFilter}
              onChange={(e) => setMinScoreFilter(Number(e.target.value))}
              className="w-full bg-[#0a0d14] border border-gaming-border rounded-xl px-3 py-2 text-slate-300 focus:border-purple-500 focus:outline-none"
            >
              <option value={0}>All Quality Scores</option>
              <option value={75}>Score ≥ 75%</option>
              <option value={85}>Score ≥ 85% (High Quality)</option>
              <option value={90}>Score ≥ 90% (Elite)</option>
            </select>
          </div>
        </div>

        {/* Library Table */}
        <div className="overflow-x-auto rounded-xl border border-gaming-border bg-[#0a0d14]">
          <table className="w-full text-left text-xs text-slate-300">
            <thead className="bg-[#121826] text-[11px] font-bold uppercase tracking-wider text-slate-400 border-b border-gaming-border select-none">
              <tr>
                <th className="p-3 w-10">
                  <input
                    type="checkbox"
                    checked={filteredClips.length > 0 && selectedClipIds.length === filteredClips.length}
                    onChange={toggleSelectAll}
                    className="rounded border-slate-700 bg-slate-900 text-purple-600 focus:ring-0"
                  />
                </th>
                <th className="p-3">Clip</th>
                <th className="p-3">Detected Game</th>
                <th className="p-3">Duration</th>
                <th className="p-3">Video Quality</th>
                <th className="p-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {filteredClips.map((c) => {
                const isSelected = selectedClipIds.includes(c.id)
                const isActive = selectedClipId === c.id

                return (
                  <tr
                    key={c.id}
                    className={`hover:bg-slate-800/40 transition ${isActive ? 'bg-purple-950/20' : ''}`}
                  >
                    <td className="p-3">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => toggleSelectClip(c.id)}
                        className="rounded border-slate-700 bg-slate-900 text-purple-600 focus:ring-0"
                      />
                    </td>
                    <td className="p-3">
                      <div className="font-bold text-slate-100 truncate max-w-xs">{c.title}</div>
                      <div className="text-[10px] text-slate-500 font-mono">Clip #{c.id}</div>
                    </td>
                    <td className="p-3">
                      <span className="bg-slate-800 text-purple-300 px-2 py-0.5 rounded font-mono text-[11px]">
                        {c.detected_game || 'Unknown Game'}
                      </span>
                    </td>
                    <td className="p-3 font-mono text-slate-400">{c.duration_sec || 60}s</td>
                    <td className="p-3">
                      <span className="font-mono text-emerald-400 font-bold">{c.quality_score}%</span>
                    </td>
                    <td className="p-3">
                      <button
                        onClick={() => setSelectedClipId(c.id)}
                        className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-purple-300 rounded-lg text-xs font-semibold border border-slate-700 transition"
                      >
                        Open Workspace
                      </button>
                    </td>
                  </tr>
                )
              })}
              {filteredClips.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-8 text-center text-slate-500 italic">
                    No clips match your search and filter criteria.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
